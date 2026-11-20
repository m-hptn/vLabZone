"""E06 – Can a user fake someone else's tunnel IP?

WireGuard cryptokey routing: the edge accepts packets from a peer only with the
source IP registered for exactly that key. Spoofed sources and unregistered keys
are dropped.
"""
import pytest

from tests.conftest import ALICE, BOB, ident, in_setup
from tests.lib.results import write_protocol
from tests.lib.wg import tunnel_ip

pytestmark = pytest.mark.stage1


def test_e06_spoofed_tunnel_ip(lab, tunnel):
    bob = tunnel("ws-power", BOB, 12)
    # Alice's key is registered for 10.250.1.12, but she sends as Bob (10.250.2.12)
    alice = tunnel("ws-std", ALICE, 12, ip=tunnel_ip(BOB, 12))

    spoofed = ident(alice.curl("http://192.168.1.20", timeout=3))
    legit = ident(bob.curl("http://192.168.1.20"))

    write_protocol("E06a", "Spoofed tunnel IP", spoofed is None and legit is not None,
                   {"spoofed_answer": spoofed, "bob_answer": legit})
    assert in_setup(legit, 12), "Bob's own access must keep working"
    assert spoofed is None, "edge accepted a packet with a foreign tunnel IP"


def test_e06_unregistered_key(lab, tunnel):
    edge = lab["lab-edge"]
    t = tunnel("ws-std", ALICE, 7, register=False)

    answer = ident(t.curl("http://192.168.1.20", timeout=3))
    peers = edge.run("edge-ctl peer list --setup 7").stdout

    write_protocol("E06b", "Unregistered key", answer is None, {"answer": answer})
    assert answer is None, "setup 7 reachable without a registered key"
    assert t.pubkey not in peers
