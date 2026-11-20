"""E01 – Does the one-time corporate allowance suffice?

corp-fw only allows UDP 51800-52099 to the edge and TCP 443/8443 to the broker.
ws-std reaches http://192.168.1.20 in setup 12; on the corporate side only
encrypted WireGuard UDP is visible.
"""
import pytest

from tests.conftest import ALICE, ident
from tests.lib.capture import capture
from tests.lib.results import write_protocol

pytestmark = pytest.mark.stage1


def test_e01_access_through_allowance(lab, tunnel):
    corp_fw = lab["corp-fw"]
    t = tunnel("ws-std", ALICE, 12)

    with capture(corp_fw, "eth10", name="e01-corp-fw-eth10") as cap:
        answer = ident(t.curl("http://192.168.1.20"))

    assert answer is not None, "no answer from 192.168.1.20 through the tunnel"
    assert answer["setup"] == 12 and answer["device"] == "dev-12-20"

    non_wg = [l for l in cap.lines if not (" UDP" in l and ".518" in l)]
    assert cap.lines, "nothing captured on corp-fw eth10"
    assert not non_wg, f"non-WireGuard traffic on the corporate side: {non_wg[:3]}"
    assert not any("http" in l.lower() or ".80:" in l for l in cap.lines)

    broker = lab["ws-std"].run("curl -s -m 5 http://10.20.98.2:8443/healthz", check=False)
    assert broker.returncode == 0 and '"ok"' in broker.stdout, "broker port 8443 not reachable"

    write_protocol("E01", "One-time corporate allowance", True,
                   {"captured_packets": len(cap.lines), "non_wireguard_packets": len(non_wg)},
                   observations=f"pcap: {cap.pcap.name}; sample: {cap.lines[:2]}",
                   consequence="UDP 51800-52099 + TCP 443/8443 suffice for access")
