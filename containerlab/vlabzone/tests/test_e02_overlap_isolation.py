"""E02 – Are setups with the same network cleanly separated?

Real world: setups are groups of devices on one system switch, several setups per
switch, and devices on one switch may share an IP. So isolation is tested where it
matters most – between two setups on the SAME system switch – and across switches.
"""
import json

import pytest

from tests.conftest import ALICE, BOB, CI, EDGE_MODE, ident, in_setup
from tests.lib import labcfg
from tests.lib.capture import capture
from tests.lib.results import write_protocol

pytestmark = pytest.mark.stage1


def port_of(device: str) -> str:
    return labcfg.device(device)["port"]


def test_e02_same_switch_same_ip(lab, tunnel):
    """Setup 12 and setup 7 both live on sys1, both have a device at 192.168.1.20."""
    alice = tunnel("ws-std", ALICE, 12)
    bob = tunnel("ws-power", BOB, 7)
    sys1 = lab["lab-sys1"]
    port7 = port_of(labcfg.device_at(7, "192.168.1.20"))   # access port of setup 7's .20

    with capture(sys1, port7, "tcp port 80", name="e02-sys1-setup7-port") as cap7:
        a = ident(alice.curl("http://192.168.1.20"))
    # positive control: the same capture does see Bob's request on that port
    with capture(sys1, port7, "tcp port 80", name="e02-sys1-setup7-port-control") as control:
        b = ident(bob.curl("http://192.168.1.20"))
    assert control.lines, f"capture on lab-sys1 {port7} sees nothing – test would be meaningless"

    assert in_setup(a, 12), a
    assert in_setup(b, 7), b
    assert a["switch"] == b["switch"] == "sys1" and a["ip"] == b["ip"]
    assert cap7.lines == [], f"Alice's request showed up in setup 7: {cap7.lines[:3]}"

    write_protocol("E02", "Two setups on one system switch, same IP", True,
                   {"alice_setup12": a, "bob_setup7": b, "packets_on_setup7_port": len(cap7.lines)},
                   consequence="VLANs on the system switch keep identical devices of different setups apart")


def test_e02_across_switches(lab, tunnel):
    """Setup 5 on sys2 – same IP again; nothing of it crosses into sys1 or vice versa."""
    alice = tunnel("ws-std", ALICE, 12)
    ci = tunnel("ws-ci", CI, 5)
    agg = lab["lab-agg"]
    sys1_uplink = labcfg.CFG["switches"][0]["agg_port"]

    with capture(agg, sys1_uplink, "tcp port 80", name="e02-agg-sys1-uplink") as cap:
        c = ident(ci.curl("http://192.168.1.20"))
    # positive control: Alice's request (setup 12 on sys1) is seen on that trunk
    with capture(agg, sys1_uplink, "tcp port 80", name="e02-agg-sys1-uplink-control") as control:
        a = ident(alice.curl("http://192.168.1.20"))
    assert control.lines, "capture on the sys1 trunk sees nothing – test would be meaningless"

    assert in_setup(c, 5) and c["switch"] == "sys2", c
    assert in_setup(a, 12), a
    assert cap.lines == [], f"setup 5 traffic on the trunk to sys1: {cap.lines[:3]}"
    write_protocol("E02b", "Setups on different system switches", True, {"ci_setup5": c, "alice_setup12": a})


def test_e02_trunks_carry_only_needed_vlans(lab):
    """Each trunk carries exactly the VLANs behind it – no setup VLAN where it isn't needed."""
    def vlans(node: str, port: str) -> set[int]:
        data = json.loads(lab[node].run(f"bridge -j vlan show dev {port}").stdout)
        return {v["vlan"] for v in data[0]["vlans"]} if data else set()

    actual, expected = {}, {}
    for sw in ["agg"] + [s["name"] for s in labcfg.CFG["switches"]]:
        for kind, port, *rest in labcfg.switch_config(sw, EDGE_MODE):
            if kind == "trunk":
                actual[f"lab-{sw}:{port}"] = sorted(vlans(f"lab-{sw}", port))
                expected[f"lab-{sw}:{port}"] = sorted(rest[0])
    write_protocol("E02c", "Trunk pruning", actual == expected, {"trunks": actual})
    assert actual == expected
