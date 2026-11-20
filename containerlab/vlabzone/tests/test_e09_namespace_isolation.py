"""E09 – Does the namespace isolate the power-user context from the corporate network?

In ws-power, the context for setup 12 lives in its own network namespace. Inside it
only setup 12 is reachable – not corp-svc, not setup 7 – even if the user adds a
default route into the tunnel.
"""
import pytest

from tests.conftest import BOB, ident
from tests.lib.results import write_protocol

pytestmark = pytest.mark.stage1


def test_e09_namespace_only_reaches_its_setup(lab, tunnel):
    ws = lab["ws-power"]
    t = tunnel("ws-power", BOB, 12, netns="setup12")

    inside = ident(t.curl("http://192.168.1.20"))
    corp_direct = t.curl("http://10.20.20.10", timeout=3)

    # the user tries harder: everything into the tunnel
    t.run("ip route add default dev wg-s12")
    corp_via_tunnel = t.curl("http://10.20.20.10", timeout=3)

    outside = ident(ws.run("curl -s -m 5 http://10.20.20.10", check=False))

    results = {
        "setup12_in_namespace": inside,
        "corp_svc_direct": corp_direct.returncode == 0,
        "corp_svc_via_tunnel": corp_via_tunnel.returncode == 0,
        "corp_svc_outside_namespace": outside,
    }
    ok = (inside and inside["setup"] == 12 and corp_direct.returncode != 0
          and corp_via_tunnel.returncode != 0 and outside and outside["setup"] == "corp")
    write_protocol("E09", "Namespace isolates the power-user context", bool(ok), results)

    assert inside and inside["setup"] == 12
    assert corp_direct.returncode != 0, "corp-svc reachable from inside the namespace"
    assert corp_via_tunnel.returncode != 0, "edge forwarded tunnel traffic to the corporate network"
    assert outside and outside["setup"] == "corp", "workspace itself must still reach corp-svc"
