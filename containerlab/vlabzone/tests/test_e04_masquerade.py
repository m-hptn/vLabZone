"""E04 – Does access work despite the wrong gateway on the devices?

Devices use 192.168.1.1 (does not exist) as default gateway. With MASQUERADE
they answer to 192.168.1.254 on their own subnet; without it, replies to
10.250.x.y go to the non-existent gateway and are lost.
"""
import pytest

from tests.conftest import ALICE, ident, in_setup
from tests.lib.results import write_protocol

pytestmark = pytest.mark.stage1


def test_e04_masquerade_is_required(lab, tunnel, nat, edge_mode):
    t = tunnel("ws-std", ALICE, 12)

    with_nat = ident(t.curl("http://192.168.1.20"))
    nat(12, False)   # edge (VRF) or gw-12 (PBR)
    try:
        without_nat = ident(t.curl("http://192.168.1.20", timeout=3))
    finally:
        nat(12, True)
    restored = ident(t.curl("http://192.168.1.20"))

    assert with_nat and with_nat["peer"] == "192.168.1.254"
    assert without_nat is None, "device answered without MASQUERADE – gateway not as wrong as intended?"
    assert in_setup(restored, 12), restored

    write_protocol("E04", "Access despite wrong device gateway", True,
                   {"with_nat": with_nat, "without_nat": without_nat},
                   setup_notes=f"MASQUERADE on {'lab-edge' if edge_mode == 'vrf' else 'gw-12'}",
                   consequence="MASQUERADE on the setup VLAN is mandatory")
