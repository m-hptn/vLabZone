"""E13 – Alias mode for CI.

ws-ci holds contexts to setup 12 and setup 7 in ONE namespace. Both setups use
192.168.1.0/24, so the edge offers every setup N also as 10.200.N.0/24 (DNAT of the whole
prefix, only for setup N's own tunnels 10.250.N.0/24). The workspace routes 10.200.12.0/24
into the setup-12 tunnel and 10.200.7.0/24 into the setup-7 tunnel – no overlapping routes.

Known limit (documented, not tested): protocols carrying IP addresses in the payload
(active FTP, SIP) see the real 192.168.1.x addresses.
"""
import pytest

from tests.conftest import CI, ident
from tests.lib import labcfg
from tests.lib.results import write_protocol

pytestmark = pytest.mark.stage2


def test_e13_alias_parallel_access(lab, tunnel):
    for s in (12, 7):
        tunnel("ws-ci", CI, s, routes=(labcfg.alias_prefix(s),))
    ws = lab["ws-ci"]
    # every device of both setups, by its alias address
    targets = {(s, labcfg.device(d)["ip"].split("/")[0]): labcfg.alias_ip(s, labcfg.device(d)["ip"].split("/")[0])
               for s in (12, 7) for d in labcfg.setup(s)["devices"]}
    answers = {alias: ident(ws.run(f"curl -s -m 5 http://{alias}", check=False)) for alias in targets.values()}
    wrong = {alias: a for (s, ip), alias in targets.items()
             if not (a := answers[alias]) or a["device"] != labcfg.device_at(s, ip) or a["peer"] != "192.168.1.254"}
    routes = ws.run("ip route").stdout
    write_protocol("E13", "Alias mode for CI", not wrong,
                   {"answers": {k: (v["device"] if v else None) for k, v in answers.items()}},
                   setup_notes="ws-ci, two tunnels in the init namespace, routes 10.200.12.0/24 and 10.200.7.0/24",
                   observations=f"routes:\n{routes}",
                   consequence="known limit: IP addresses inside payloads (active FTP, SIP) are not translated")
    assert "10.200.12.20" in targets.values() and "10.200.7.20" in targets.values()
    assert "192.168.1.0/24" not in routes, "alias mode must not need the original network"
    assert not wrong, wrong


def test_e13_alias_of_another_setup_not_translated(lab, tunnel):
    """A setup-12 tunnel sending to setup 7's alias (10.200.7.20) is not translated: no path."""
    t = tunnel("ws-ci", CI, 12, routes=("10.200.7.0/24", "10.200.12.0/24"))
    own = ident(t.curl("http://10.200.12.20"))
    foreign = ident(t.curl("http://10.200.7.20", timeout=3))
    write_protocol("E13b", "Alias of another setup", foreign is None, {"own": own, "foreign": foreign})
    assert own and own["device"] == labcfg.device_at(12, "192.168.1.20")
    assert foreign is None, f"setup 7 reached through setup 12's tunnel: {foreign}"
