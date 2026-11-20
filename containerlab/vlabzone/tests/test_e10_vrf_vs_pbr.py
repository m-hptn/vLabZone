"""E10 – VRF or PBR?

All functional tests (E01–E06, E09) run against both labs (`make test-all`).
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

from tests.conftest import ALICE, ident
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
                   observations="VRF: one WireGuard peer. PBR: WireGuard peer + ip rule.")
    assert steps == (1 if edge_mode == "vrf" else 2), delta


# --- PBR ---------------------------------------------------------------------------

@pytest.fixture
def pbr_only(edge_mode):
    if edge_mode != "pbr":
        pytest.skip("PBR misconfiguration – run with EDGE_MODE=pbr")


def test_e10_pbr_missing_rule_fails_closed(lab, tunnel, pbr_only):
    edge = lab["lab-edge"]
    t = tunnel("ws-std", ALICE, 12)
    assert ident(t.curl("http://192.168.1.20"))["setup"] == 12

    edge.run(f"ip rule del from {ALICE_IP} iif wg0")
    try:
        answer = ident(t.curl("http://192.168.1.20", timeout=3))
    finally:
        edge.run(f"ip rule add from {ALICE_IP} iif wg0 lookup 1012 pref 10000")

    write_protocol("E10-pbr-missing-rule", "PBR: ip rule missing", answer is None, {"answer": answer},
                   observations="without a rule the main table is used; it has no route into any setup",
                   consequence="missing rule = no access (fail-closed)")
    assert answer is None, f"access without ip rule: {answer}"


def test_e10_pbr_wrong_table_reaches_wrong_setup(lab, tunnel, pbr_only):
    edge = lab["lab-edge"]
    t = tunnel("ws-std", ALICE, 12)

    edge.run(f"ip rule del from {ALICE_IP} iif wg0 && ip rule add from {ALICE_IP} iif wg0 lookup 1007 pref 10000")
    try:
        answer = ident(t.curl("http://192.168.1.20", timeout=3))
    finally:
        edge.run(f"ip rule del from {ALICE_IP} iif wg0 && ip rule add from {ALICE_IP} iif wg0 lookup 1012 pref 10000")

    write_protocol("E10-pbr-wrong-table", "PBR: ip rule points to the wrong table", True, {"answer": answer},
                   observations="Alice booked setup 12 but lands in setup 7 – nothing else stops it",
                   consequence="PBR is NOT fail-closed: one wrong per-booking rule gives access to a foreign setup")
    assert answer and answer["setup"] == 7, f"expected to land in setup 7, got {answer}"


def test_e10_pbr_gateways_isolated(lab, tunnel, pbr_only):
    """Over the shared transfer VLAN, neither a tunnel nor a setup reaches another setup's gateway."""
    t = tunnel("ws-std", ALICE, 12, routes=("192.168.1.0/24", "10.199.0.0/24"))
    dev = lab["dev-12-20"]
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
        with capture(lab["lab-sw"], "eth21", "tcp port 80", name="e10-vrf-wrong-vrf-eth21") as cap:
            answer = ident(t.curl("http://192.168.1.20", timeout=3))
    finally:
        handle = edge.run("nft -a list chain inet edge setups | awk '/\"eth2.107\"/ && /\"wg-s12\"/ {print $NF}'").stdout.strip()
        edge.run(f"nft delete rule inet edge setups handle {handle} && "
                 "ip link set wg-s12 master vrf-s12 && ip addr replace 10.250.0.1/16 dev wg-s12")
    # Changing the VRF membership restarts wg-s12: the edge loses the session keys and the
    # workspace only re-handshakes after WireGuard's retry timer (~15 s).
    t0 = time.monotonic()
    while not ident(t.curl("http://192.168.1.20", timeout=1)):
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
