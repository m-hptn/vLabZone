"""E10 – VRF, PBR or hybrid?

All functional tests (E01–E06, E09) run against all three labs (`make test-all`).
This module adds what differs between the modes:

  * configuration steps per booking (objects changed on the edge),
  * deliberate misconfigurations: does the edge fail closed (no access) or
    does traffic end up in the WRONG setup?

The tests assert the behaviour that was observed, so a change in behaviour is
noticed. Every case writes a protocol to results/.
"""
import json
import time

import pytest

from tests.conftest import ALICE, BOB, ident, in_setup
from tests.lib import labcfg
from tests.lib.capture import capture
from tests.lib.results import write_protocol
from tests.lib.wg import tunnel_ip

pytestmark = pytest.mark.stage2

ALICE_IP = tunnel_ip(ALICE, 12)


def edge_objects(edge) -> dict:
    """Count the per-booking objects on the edge."""
    peers = int(edge.run("wg show all peers | wc -l").stdout)
    rules = len([r for r in json.loads(edge.run("ip -j rule show").stdout) if r.get("iif") == "wg0"])
    return {"wireguard_peers": peers, "ip_rules": rules}


def test_e10_config_steps_per_booking(lab, tunnel, edge_mode):
    edge = lab["lab-edge"]
    before = edge_objects(edge)
    tunnel("ws-std", ALICE, 12)
    after = edge_objects(edge)
    delta = {k: after[k] - before[k] for k in before}
    steps = sum(delta.values())

    write_protocol("E10-steps", "Configuration steps per booking", True,
                   {"mode": edge_mode, "changed_objects": delta, "steps": steps},
                   observations="VRF: one WireGuard peer. PBR and hybrid: WireGuard peer + ip rule.")
    assert steps == (1 if edge_mode == "vrf" else 2), delta


# --- all modes ---------------------------------------------------------------------

def test_e10_setup_cannot_reach_foreign_tunnels(lab, tunnel):
    """A device of setup 12 (edge as gateway) cannot reach a workspace booked into setup 7.
    In hybrid mode every setup table holds 10.250.0.0/16 dev wg0 (the way back to the
    tunnels), in PBR gw-12 routes 10.250/16 to the edge – only the forward filter stops it."""
    t7 = tunnel("ws-power", BOB, 7)
    dev = lab[labcfg.device_at(12, "192.168.1.20")]
    dev.run("ip route replace default via 192.168.1.254")
    try:
        reached = dev.run(f"ping -c1 -W2 {t7.ip}", check=False).returncode == 0
    finally:
        dev.run("ip route replace default via 192.168.1.1")
    write_protocol("E10-foreign-tunnel", "Setup cannot reach tunnels of other setups", not reached,
                   {"target": t7.ip, "reached": reached})
    assert not reached


# --- PBR and hybrid: one ip rule per booking ----------------------------------------

@pytest.fixture
def rules_mode(edge_mode):
    if edge_mode == "vrf":
        pytest.skip("per-booking ip rules only exist in PBR and hybrid mode")


@pytest.fixture
def pbr_only(edge_mode):
    if edge_mode != "pbr":
        pytest.skip("PBR misconfiguration – run with EDGE_MODE=pbr")


@pytest.fixture
def hybrid_only(edge_mode):
    if edge_mode != "hybrid":
        pytest.skip("hybrid misconfiguration – run with EDGE_MODE=hybrid")


def set_rule(edge, table: int) -> None:
    edge.run(f"ip rule del from {ALICE_IP} iif wg0; ip rule add from {ALICE_IP} iif wg0 lookup {table} pref 10000")


def test_e10_rule_missing_fails_closed(lab, tunnel, rules_mode, edge_mode):
    edge = lab["lab-edge"]
    t = tunnel("ws-std", ALICE, 12)
    assert in_setup(ident(t.curl("http://192.168.1.20")), 12)

    edge.run(f"ip rule del from {ALICE_IP} iif wg0")
    try:
        answer = ident(t.curl("http://192.168.1.20", timeout=3))
    finally:
        edge.run(f"ip rule add from {ALICE_IP} iif wg0 lookup 1012 pref 10000")

    write_protocol(f"E10-{edge_mode}-missing-rule", f"{edge_mode}: ip rule missing", answer is None, {"answer": answer},
                   observations="without a rule the catch-all `iif wg0 unreachable` (pref 30000) applies",
                   consequence="missing rule = no access (fail-closed)")
    assert answer is None, f"access without ip rule: {answer}"


def test_e10_rule_without_route_never_reaches_main(lab, tunnel, rules_mode, edge_mode):
    """A rule into a table without a route (setup removed, typo) must not fall through to the
    main table – that holds the corporate routes (RouterOS trial: `action=lookup` leaked there)."""
    edge = lab["lab-edge"]
    tunnel("ws-std", ALICE, 12)
    probe = f"ip route get 10.20.20.10 from {ALICE_IP} iif wg0"
    set_rule(edge, 1099)                       # table without any route
    try:
        no_route = edge.run(probe, check=False)
    finally:
        set_rule(edge, 1012)
    ok_route = edge.run(probe, check=False)
    result = (no_route.stdout + no_route.stderr).strip()
    write_protocol(f"E10-{edge_mode}-empty-table", f"{edge_mode}: rule into a table without route",
                   "unreachable" in result, {"empty_table": result, "correct_table": (ok_route.stdout + ok_route.stderr).strip()},
                   consequence="the catch-all rule stops the fall-through; without it the main table would answer")
    assert "unreachable" in result, f"fell through: {result}"


def test_e10_pbr_wrong_table_reaches_wrong_setup(lab, tunnel, pbr_only):
    edge = lab["lab-edge"]
    t = tunnel("ws-std", ALICE, 12)

    edge.run(f"ip rule del from {ALICE_IP} iif wg0 && ip rule add from {ALICE_IP} iif wg0 lookup 1007 pref 10000")
    try:
        answer = ident(t.curl("http://192.168.1.20", timeout=3))
    finally:
        edge.run(f"ip rule del from {ALICE_IP} iif wg0 && ip rule add from {ALICE_IP} iif wg0 lookup 1012 pref 10000")

    write_protocol("E10-pbr-wrong-table", "PBR: ip rule points to the wrong table", True, {"answer": answer},
                   observations="Alice booked setup 12 but lands in setup 7 (same system switch) – nothing else stops it",
                   consequence="PBR is NOT fail-closed: one wrong per-booking rule gives access to a foreign setup")
    assert in_setup(answer, 7), f"expected to land in setup 7, got {answer}"


def test_e10_hybrid_wrong_table_fails_closed(lab, tunnel, hybrid_only):
    """Same mistake as in PBR. The per-setup forward rule (tunnel IP 10.250.x.N may only enter
    setup N's VLAN) drops it. Control: without that rule the mistake leads into setup 7."""
    edge = lab["lab-edge"]
    t = tunnel("ws-std", ALICE, 12)
    set_rule(edge, 1007)
    permissive = None
    try:
        guarded = ident(t.curl("http://192.168.1.20", timeout=3))
        # control: what the safeguard prevents
        edge.run('nft insert rule inet edge setups iifname "wg0" oifname "eth2.107" accept comment "e10-control"')
        permissive = ident(t.curl("http://192.168.1.20", timeout=3))
    finally:
        handle = edge.run("nft -a list chain inet edge setups | awk '/e10-control/ {print $NF}'").stdout.strip()
        if handle:
            edge.run(f"nft delete rule inet edge setups handle {handle}")
        set_rule(edge, 1012)
    restored = ident(t.curl("http://192.168.1.20"))
    write_protocol("E10-hybrid-wrong-table", "hybrid: ip rule points to the wrong table", guarded is None,
                   {"with_safeguard": guarded, "without_safeguard": permissive, "restored": restored},
                   observations="the per-setup forward rule matches the setup number in the tunnel address",
                   consequence="a wrong per-booking rule fails closed; only a wrong tunnel ADDRESS (consistent "
                               "with the rule) would still lead into a foreign setup")
    assert guarded is None, f"wrong table reached a setup despite the safeguard: {guarded}"
    assert in_setup(permissive, 7), f"control: expected setup 7 without the safeguard, got {permissive}"
    assert in_setup(restored, 12), restored


def test_e10_pbr_gateways_isolated(lab, tunnel, pbr_only):
    """Over the shared transfer VLAN, neither a tunnel nor a setup reaches another setup's gateway."""
    t = tunnel("ws-std", ALICE, 12, routes=("192.168.1.0/24", "10.199.0.0/24"))
    dev = lab[labcfg.device_at(12, "192.168.1.20")]
    dev.run("ip route replace default via 192.168.1.254")
    try:
        attempts = {
            "tunnel -> gw-7 10.199.0.7": t.run("ping -c1 -W2 10.199.0.7", check=False).returncode == 0,
            "tunnel -> edge 10.199.0.1": t.run("ping -c1 -W2 10.199.0.1", check=False).returncode == 0,
            "setup 12 -> gw-7 10.199.0.7": dev.run("ping -c1 -W2 10.199.0.7", check=False).returncode == 0,
            "setup 12 -> edge 10.199.0.1": dev.run("ping -c1 -W2 10.199.0.1", check=False).returncode == 0,
        }
    finally:
        dev.run("ip route replace default via 192.168.1.1")
    open_paths = [k for k, v in attempts.items() if v]
    write_protocol("E10-pbr-transfer", "PBR: gateways isolated on the transfer VLAN", not open_paths, attempts)
    assert not open_paths, open_paths


# --- VRF ---------------------------------------------------------------------------

@pytest.fixture
def vrf_only(edge_mode):
    if edge_mode != "vrf":
        pytest.skip("VRF misconfiguration – run with EDGE_MODE=vrf")


def test_e10_vrf_peer_on_wrong_setup_fails_closed(lab, tunnel, vrf_only):
    """The broker registers Alice's key on setup 7, but her agent connects to setup 12."""
    edge = lab["lab-edge"]
    t = tunnel("ws-std", ALICE, 12, register=False)
    edge.run(f"edge-ctl peer add --setup 7 --pubkey {t.pubkey} --ip {ALICE_IP}")
    try:
        answer = ident(t.curl("http://192.168.1.20", timeout=3))
    finally:
        edge.run(f"edge-ctl peer del --setup 7 --pubkey {t.pubkey}")

    write_protocol("E10-vrf-wrong-peer", "VRF: peer registered on the wrong setup", answer is None,
                   {"answer": answer},
                   observations="the key is unknown on wg-s12, so the handshake fails",
                   consequence="per-booking mistakes in VRF mode fail closed")
    assert answer is None


def test_e10_vrf_missing_forward_rule_fails_closed(lab, tunnel, vrf_only):
    edge = lab["lab-edge"]
    t = tunnel("ws-std", ALICE, 12)
    handle = edge.run("nft -a list chain inet edge setups | awk '/\"wg-s12\"/ {print $NF}'").stdout.strip()
    edge.run(f"nft delete rule inet edge setups handle {handle}")
    try:
        answer = ident(t.curl("http://192.168.1.20", timeout=3))
    finally:
        edge.run('nft add rule inet edge setups meta sdifname "wg-s12" oifname "eth2.112" accept')

    write_protocol("E10-vrf-missing-rule", "VRF: forward rule missing", answer is None, {"answer": answer},
                   consequence="missing firewall rule = no access (fail-closed)")
    assert answer is None


def test_e10_vrf_tunnel_in_wrong_vrf_leaks_one_way(lab, tunnel, vrf_only):
    """Static misconfiguration: wg-s12 enslaved to vrf-s7 (set once per setup, not per booking).

    Observed: requests reach setup 7, but the replies are routed to wg-s7 (it already
    owns 10.250.0.0/16 in vrf-s7) and dropped by WireGuard there. A one-way leak:
    TCP fails, but UDP or other one-way traffic would reach foreign devices.
    """
    edge = lab["lab-edge"]
    t = tunnel("ws-std", ALICE, 12)
    # the forward rule is per interface pair, so the misconfiguration needs both to be wrong
    moved_at = int(edge.run("date +%s").stdout)
    edge.run('ip link set wg-s12 master vrf-s7 && '
             'nft add rule inet edge setups meta sdifname "wg-s12" oifname "eth2.107" accept')
    try:
        # the move restarts wg-s12 (session lost): wait for the workspace's new handshake
        t0 = time.monotonic()
        while int(edge.run("wg show wg-s12 latest-handshakes | awk '{print $2}'").stdout or 0) <= moved_at:
            assert time.monotonic() - t0 < 40, "no new handshake after the move"
            t.curl("http://192.168.1.20", timeout=1)
        port7 = labcfg.device(labcfg.device_at(7, "192.168.1.20"))["port"]
        with capture(lab["lab-sys1"], port7, "tcp port 80", name="e10-vrf-wrong-vrf-setup7-port") as cap:
            answer = ident(t.curl("http://192.168.1.20", timeout=3))
    finally:
        handle = edge.run("nft -a list chain inet edge setups | awk '/\"eth2.107\"/ && /\"wg-s12\"/ {print $NF}'").stdout.strip()
        edge.run(f"nft delete rule inet edge setups handle {handle} && "
                 "ip link set wg-s12 master vrf-s12 && ip addr replace 10.250.0.1/16 dev wg-s12")
    # Changing the VRF membership restarts wg-s12: the edge loses the session keys and the
    # workspace only re-handshakes after WireGuard's retry timer (~15 s).
    t0 = time.monotonic()
    while not in_setup(ident(t.curl("http://192.168.1.20", timeout=1)), 12):
        assert time.monotonic() - t0 < 40, "edge not restored within 40 s"
    recovery = round(time.monotonic() - t0, 1)

    leaked = [l for l in cap.lines if "> 192.168.1.20.80" in l]
    write_protocol("E10-vrf-wrong-vrf", "VRF: tunnel interface in the wrong VRF", True,
                   {"answer": answer, "packets_into_setup7": len(leaked), "recovery_after_fix_s": recovery},
                   observations="requests reach setup 7, replies go to wg-s7 (owner of 10.250/16 in vrf-s7) "
                                "and are dropped: one-way leak. Needs two consistent setup-level mistakes "
                                "(VRF membership AND forward rule); with no other tunnel in the target VRF "
                                "the leak would be two-way",
                   consequence="VRF can leak too, but only through setup-level misconfiguration that is "
                               "set once and easy to verify; per-booking mistakes fail closed. Any change to a "
                               "WireGuard interface (VRF membership, down/up) drops its sessions for ~15 s")
    assert answer is None, f"unexpected two-way access: {answer}"
    assert leaked, "expected the request to reach setup 7 (one-way leak)"
