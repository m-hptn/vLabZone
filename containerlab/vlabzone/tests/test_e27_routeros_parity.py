"""E27 – Parity of the RouterOS edge (MikroTik) with the Linux edge.

The functional suites (E01–E06, E09, E11, E13, E20) run unchanged against the labs
vlabzone-<mode>-ros (`make test EDGE_IMPL=routeros EDGE_MODE=…`). E10 changes Linux internals
(ip rule, nft) and is skipped there; this module makes the same mistakes on RouterOS through
the REST API (drivers/edge_routeros.py) and expects the same outcome as on Linux.
"""
import time

import pytest

from tests.conftest import ALICE, EDGE_IMPL, LAB, ident, in_setup
from tests.lib import labcfg
from tests.lib.results import write_protocol
from tests.lib.wg import tunnel_ip

pytestmark = [pytest.mark.stage2, pytest.mark.routeros,
              pytest.mark.skipif(EDGE_IMPL != "routeros", reason="RouterOS edge only (EDGE_IMPL=routeros)")]

ALICE_IP = tunnel_ip(ALICE, 12)
APPLY = 3          # RouterOS applies filter and routing-rule changes asynchronously (stage 1b: ~3 s)


@pytest.fixture
def ros(lab, edge_mode):
    import importlib.util
    spec = importlib.util.spec_from_file_location("edge_routeros", labcfg.ROOT / "drivers" / "edge_routeros.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.Edge(LAB, edge_mode).ros


def find(ros, path, **match):
    return [i for i in ros.get(path) if all(i.get(k) == v for k, v in match.items())]


def curl(t, timeout=3):
    return ident(t.curl("http://192.168.1.20", timeout=timeout))


def test_e27_config_steps_per_booking(lab, tunnel, ros, edge_mode):
    before = (len(ros.get("interface/wireguard/peers")), len(ros.get("routing/rule")))
    tunnel("ws-std", ALICE, 12)
    after = (len(ros.get("interface/wireguard/peers")), len(ros.get("routing/rule")))
    delta = {"peers": after[0] - before[0], "routing_rules": after[1] - before[1]}
    write_protocol("E27-steps", "RouterOS: changes per booking", True, {"mode": edge_mode, **delta})
    assert sum(delta.values()) == (2 if edge_mode == "pbr" else 1), delta


def test_e27_rule_missing_fails_closed(lab, tunnel, ros, edge_mode):
    if edge_mode == "vrf":
        pytest.skip("no routing rules in VRF mode")
    t = tunnel("ws-std", ALICE, 12)
    assert in_setup(curl(t, 5), 12)
    src = f"{ALICE_IP}/32" if edge_mode == "pbr" else labcfg.setup_prefix(12)
    rule = find(ros, "routing/rule", **{"src-address": src})[0]
    ros.delete("routing/rule", rule[".id"])
    try:
        time.sleep(APPLY)
        answer = curl(t)
    finally:
        keep = {k: rule[k] for k in ("src-address", "interface", "table", "action") if k in rule}
        ros.put("routing/rule", **keep, comment=rule.get("comment", ""))
    time.sleep(APPLY)
    write_protocol(f"E27-{edge_mode}-missing-rule", f"RouterOS {edge_mode}: routing rule missing", answer is None,
                   {"answer": answer})
    assert answer is None, answer
    assert in_setup(curl(t, 5), 12)


def set_table(ros, src, table):
    rule = find(ros, "routing/rule", **{"src-address": src})[0]
    ros.delete("routing/rule", rule[".id"])
    ros.put("routing/rule", **{"src-address": src}, interface="wg0", table=table,
            action="lookup-only-in-table", comment=rule.get("comment", ""))
    time.sleep(APPLY)


def test_e27_pbr_wrong_table_reaches_wrong_setup(lab, tunnel, ros, edge_mode):
    if edge_mode != "pbr":
        pytest.skip("PBR only")
    t = tunnel("ws-std", ALICE, 12)
    set_table(ros, f"{ALICE_IP}/32", "s7")
    try:
        answer = curl(t)
    finally:
        set_table(ros, f"{ALICE_IP}/32", "s12")
    write_protocol("E27-pbr-wrong-table", "RouterOS PBR: rule points to the wrong table", True, {"answer": answer},
                   consequence="same as Linux: a wrong per-booking rule gives access to a foreign setup")
    assert in_setup(answer, 7), answer


def test_e27_hybrid_wrong_table_fails_closed(lab, tunnel, ros, edge_mode):
    """Setup 12's static rule into vrf-s7: the forward rule (packet mark to-s7 + source 10.250.7.0/24)
    drops it. Control: the same rule without the source prefix lets it into setup 7."""
    if edge_mode != "hybrid":
        pytest.skip("hybrid only")
    src = labcfg.setup_prefix(12)
    t = tunnel("ws-std", ALICE, 12)
    fwd7 = find(ros, "ip/firewall/filter", comment="vlab: setup 7")[0]
    set_table(ros, src, "vrf-s7")
    permissive, control = None, None
    try:
        guarded = curl(t)
        # control: setup 7's forward rule without the source prefix, placed in front of it
        control = ros.put("ip/firewall/filter", chain="forward", action="accept", **{
            "in-interface": "wg0", "packet-mark": "to-s7", "place-before": fwd7[".id"]}, comment="e27-control")
        time.sleep(APPLY)
        permissive = curl(t)
    finally:
        if control:
            ros.delete("ip/firewall/filter", control[".id"])
        set_table(ros, src, "vrf-s12")
    restored = curl(t, 5)
    write_protocol("E27-hybrid-wrong-table", "RouterOS hybrid: setup rule points to the wrong VRF", guarded is None,
                   {"with_safeguard": guarded, "without_safeguard": permissive, "restored": restored})
    assert guarded is None, guarded
    assert in_setup(permissive, 7), permissive
    assert in_setup(restored, 12), restored


def test_e27_forward_rule_missing_fails_closed(lab, tunnel, ros, edge_mode):
    if edge_mode == "pbr":
        pytest.skip("PBR: one forward rule for all setups (tunnels -> transfer VLAN)")
    t = tunnel("ws-std", ALICE, 12)
    fwd12 = find(ros, "ip/firewall/filter", comment="vlab: setup 12")[0]
    ros.patch("ip/firewall/filter", fwd12[".id"], disabled="true")
    try:
        time.sleep(APPLY)
        answer = curl(t)
    finally:
        ros.patch("ip/firewall/filter", fwd12[".id"], disabled="false")
        time.sleep(APPLY)
    write_protocol(f"E27-{edge_mode}-missing-forward", f"RouterOS {edge_mode}: forward rule of setup 12 missing",
                   answer is None, {"answer": answer})
    assert answer is None, answer
    assert in_setup(curl(t, 5), 12)
