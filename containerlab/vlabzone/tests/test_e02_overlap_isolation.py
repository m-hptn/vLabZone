"""E02 – Are setups with the same network cleanly separated?

Alice books setup 12, Bob setup 7. Both open 192.168.1.20 and must land in
their own setup; no packet of Alice's may appear on setup 7's switch ports.
"""
import pytest

from tests.conftest import ALICE, BOB, ident
from tests.lib.capture import capture
from tests.lib.results import write_protocol

pytestmark = pytest.mark.stage1


def test_e02_same_ip_different_setups(lab, tunnel):
    alice = tunnel("ws-std", ALICE, 12)
    bob = tunnel("ws-power", BOB, 7)
    sw = lab["lab-sw"]

    with capture(sw, "eth21", "tcp port 80", name="e02-lab-sw-eth21") as cap7:
        a = ident(alice.curl("http://192.168.1.20"))
    # positive control: the same capture does see Bob's request on setup 7's port
    with capture(sw, "eth21", "tcp port 80", name="e02-lab-sw-eth21-control") as control:
        b = ident(bob.curl("http://192.168.1.20"))
    assert control.lines, "capture on lab-sw eth21 sees nothing – test would be meaningless"

    assert a and a["setup"] == 12 and a["device"] == "dev-12-20", a
    assert b and b["setup"] == 7 and b["device"] == "dev-7-20", b
    assert cap7.lines == [], f"Alice's request showed up in setup 7: {cap7.lines[:3]}"

    write_protocol("E02", "Overlapping setups are isolated", True,
                   {"alice": a, "bob": b, "packets_on_setup7_port": len(cap7.lines)},
                   consequence="VRF per setup keeps identical networks apart")
