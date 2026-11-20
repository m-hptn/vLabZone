"""E20 – Moving devices between setups (regrouping on a system switch).

Setups are dynamic groups of devices on one system switch. Regrouping = changing the
VLAN of a device's access port (`sw-port`, later the broker's switch driver for the
real Lancom switches). Devices on one switch may share an IP, so grouping two of them
into the same setup creates an address conflict the broker must refuse.
"""
import re

import pytest

from tests.conftest import ALICE, BOB, ident
from tests.lib import labcfg
from tests.lib.capture import capture
from tests.lib.results import write_protocol

pytestmark = pytest.mark.stage2

VLAN = {s["id"]: s["vlan"] for s in labcfg.CFG["setups"]}


def who(t, ip: str) -> str | None:
    a = ident(t.curl(f"http://{ip}", timeout=2))
    return a["device"] if a else None


def test_e20_move_device_between_setups(lab, tunnel, flush_arp):
    """sys1-dev2 (.21) moves from setup 12 to setup 7 (no IP conflict there), then back."""
    sys1 = lab["lab-sys1"]
    dev = labcfg.device_at(12, "192.168.1.21")
    port = labcfg.device(dev)["port"]
    alice = tunnel("ws-std", ALICE, 12)
    bob = tunnel("ws-power", BOB, 7)
    before = {"setup12": who(alice, "192.168.1.21"), "setup7": who(bob, "192.168.1.21")}

    sys1.run(f"sw-port {port} {VLAN[7]}")
    try:
        flush_arp(12); flush_arp(7)
        moved = {"setup12": who(alice, "192.168.1.21"), "setup7": who(bob, "192.168.1.21")}
        sys1.run(f"sw-port {port} none")                        # ungroup: in no setup at all
        flush_arp(12); flush_arp(7)
        ungrouped = {"setup12": who(alice, "192.168.1.21"), "setup7": who(bob, "192.168.1.21")}
    finally:
        sys1.run(f"sw-port {port} {VLAN[12]}")
        flush_arp(12); flush_arp(7)
    after = {"setup12": who(alice, "192.168.1.21"), "setup7": who(bob, "192.168.1.21")}

    write_protocol("E20a", "Move a device between setups on one switch", True,
                   {"device": dev, "before": before, "moved_to_7": moved, "ungrouped": ungrouped, "restored": after},
                   observations="one sw-port call moves the device; the setup gateways must forget "
                                "the old neighbour entry (ARP) for immediate effect",
                   consequence="regrouping = access-port VLAN change; broker must also flush/ignore stale ARP")
    assert before == {"setup12": dev, "setup7": None}
    assert moved == {"setup12": None, "setup7": dev}
    assert ungrouped == {"setup12": None, "setup7": None}
    assert after == before


def test_e20_same_ip_in_one_setup_conflicts(lab, tunnel, flush_arp):
    """Grouping sys1-dev1 (.20) into setup 7, which already has sys1-dev3 (.20): two devices
    answer ARP for the same address – the conflict the broker must refuse."""
    sys1 = lab["lab-sys1"]
    dev = labcfg.device_at(12, "192.168.1.20")
    port = labcfg.device(dev)["port"]
    bob = tunnel("ws-power", BOB, 7)

    sys1.run(f"sw-port {port} {VLAN[7]}")
    try:
        flush_arp(7)
        with capture(sys1, "eth1", f"-e vlan {VLAN[7]} and arp", name="e20-conflict-sys1-uplink") as cap:
            answer = who(bob, "192.168.1.20")
    finally:
        sys1.run(f"sw-port {port} {VLAN[12]}")
        flush_arp(7); flush_arp(12)

    macs = {m for l in cap.lines for m in re.findall(r"192\.168\.1\.20 is-at (\S+),", l)}
    write_protocol("E20b", "Same IP twice in one setup", len(macs) == 2,
                   {"answering_macs": sorted(macs), "http_answer_from": answer},
                   observations="both devices answer ARP; which one gets the traffic depends on "
                                "which reply arrives last – non-deterministic",
                   consequence="broker must refuse grouping devices with the same IP into one setup")
    assert len(macs) == 2, f"expected two ARP replies for 192.168.1.20, got {cap.lines}"
    assert who(bob, "192.168.1.20") == labcfg.device_at(7, "192.168.1.20"), "setup 7 not restored"
