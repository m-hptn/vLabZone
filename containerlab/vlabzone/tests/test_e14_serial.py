"""E14 – Serial consoles on the shared serial device server (Moxa NPort 5600, docs/consoles-usb.md).

The consoles of all devices are cabled to ONE NPort (nport1, appliance VLAN 10.197.0.0/24). A booked
workspace opens a console with RFC 2217 (pyserial `rfc2217://10.197.0.10:<4000+port>`) through its
setup's tunnel. The edge lets a setup reach exactly the TCP ports of its own devices' consoles
(set console_sN, network-enforced). Expected:

  - the console of an own device answers (banner of the cabled device)
  - a baud-rate change from the client arrives on the serial line (`show baud` on the console)
  - the console of another setup's device is unreachable, and so is the NPort from the setup's devices
  - one session per port at a time, like the NPort
  - regrouping a device moves its console (edge-ctl console deny/allow)
"""
import json
import threading
import time

import pytest

from tests.conftest import ALICE, BOB
from tests.lib import labcfg
from tests.lib.results import write_protocol

pytestmark = pytest.mark.stage2

APP_NET = labcfg.CFG["appliances"]["net"]
ROUTES = ("192.168.1.0/24", APP_NET)

PROBE = r"""
import json, os, time
import serial
out = {}
try:
    s = serial.serial_for_url(os.environ["URL"], baudrate=int(os.environ.get("BAUD", "9600")), timeout=1)
except Exception as e:
    print(json.dumps({"error": f"{type(e).__name__}: {e}"}))
    raise SystemExit(0)
def cmd(c, wait=0.8):
    s.reset_input_buffer()
    s.write((c + "\r").encode())
    time.sleep(wait)
    return s.read(4096).decode(errors="replace").strip()
out["prompt"] = cmd("")
out["baud"] = cmd("show baud")
if os.environ.get("BAUD2"):
    s.baudrate = int(os.environ["BAUD2"])
    time.sleep(0.5)
    out["baud2"] = cmd("show baud")
out["version"] = cmd("show version")
time.sleep(float(os.environ.get("HOLD", "0")))
s.close()
print(json.dumps(out))
"""


def console_url(device: str) -> str:
    (ip, port), = labcfg.vlab_cfg.device_consoles(labcfg.CFG, device)
    return f"rfc2217://{ip}:{port}"


def probe(t, device: str, timeout: int = 40, **env) -> dict:
    """Open <device>'s console from tunnel t and run a few console commands."""
    envs = " ".join(f"{k}={v}" for k, v in {"URL": console_url(device), **env}.items())
    r = t.run(f"{envs} python3 - <<'EOF'\n{PROBE}\nEOF", check=False, timeout=timeout)
    if r.returncode != 0 or not r.stdout.strip():
        return {"error": (r.stderr or r.stdout).strip()[-300:]}
    return json.loads(r.stdout.strip().splitlines()[-1])


def tcp_open(t, device: str, timeout: int = 3) -> bool:
    (ip, port), = labcfg.vlab_cfg.device_consoles(labcfg.CFG, device)
    return t.run(f"timeout {timeout} bash -c '</dev/tcp/{ip}/{port}'", check=False, timeout=timeout + 5).returncode == 0


def test_e14_own_console_with_baud_change(lab, tunnel):
    dev = "sys1-dev1"                                   # setup 12, NPort port 1
    t = tunnel("ws-std", ALICE, 12, routes=ROUTES)
    out = probe(t, dev, BAUD=9600, BAUD2=115200)
    ok = (f"{dev} login:" in out.get("prompt", "") and "baud 9600 8n1" in out.get("baud", "")
          and "baud 115200 8n1" in out.get("baud2", ""))
    write_protocol("E14a", "Serial console of an own device over RFC 2217", ok, out,
                   observations="the baud rate set by the client arrives on the serial line (termios of the port)")
    assert ok, out


def test_e14_foreign_console_unreachable(lab, tunnel):
    """setup 12 must not reach sys1-dev3's console (setup 7), same NPort, port 3."""
    t = tunnel("ws-std", ALICE, 12, routes=ROUTES)
    own, foreign = tcp_open(t, "sys1-dev2"), tcp_open(t, "sys1-dev3")
    ungrouped = tcp_open(t, "sys2-dev3")               # in no setup at all
    write_protocol("E14b", "Consoles of other setups' devices", own and not foreign and not ungrouped,
                   {"own_sys1-dev2": own, "foreign_sys1-dev3": foreign, "ungrouped_sys2-dev3": ungrouped})
    assert own, "control: own second console not reachable"
    assert not foreign and not ungrouped


def test_e14_devices_cannot_reach_nport(lab):
    """A setup device must not reach the appliance VLAN through its gateway (the edge's setup VRF
    holds a route to it for the tunnels; in PBR mode gw-12 has none)."""
    dev = lab["sys1-dev1"]
    dev.run("ip route replace 10.197.0.0/24 via 192.168.1.254", check=False)
    try:
        r = dev.run("timeout 3 bash -c '</dev/tcp/10.197.0.10/4001'", check=False, timeout=10)
    finally:
        dev.run("ip route del 10.197.0.0/24", check=False)
    write_protocol("E14c", "Setup device -> NPort", r.returncode != 0, {"rc": r.returncode})
    assert r.returncode != 0


def test_e14_one_session_per_port(lab, tunnel):
    dev = "sys1-dev2"
    t1 = tunnel("ws-std", ALICE, 12, routes=ROUTES)
    t2 = tunnel("ws-power", BOB, 12, netns="setup12", routes=ROUTES)
    first = {}
    th = threading.Thread(target=lambda: first.update(probe(t1, dev, HOLD=5)))
    th.start()
    time.sleep(3.5)                                      # first session is open and holding
    second = probe(t2, dev)
    th.join()
    ok = f"{dev} login:" in first.get("prompt", "") and "error" in second
    write_protocol("E14d", "One session per serial port", ok, {"first": first, "second": second},
                   consequence="console sessions are exclusive (NPort: max connections 1); the broker must "
                               "show who holds a console")
    assert ok, {"first": first, "second": second}


def test_e14_regroup_moves_console(lab, tunnel):
    """sys1-dev3 moves from setup 7 to setup 12: its console moves with it (edge-ctl console)."""
    edge, dev = lab["lab-edge"], "sys1-dev3"
    t12 = tunnel("ws-std", ALICE, 12, routes=ROUTES)
    t7 = tunnel("ws-power", BOB, 7, netns="setup7", routes=ROUTES)
    before = (tcp_open(t12, dev), tcp_open(t7, dev))
    edge.run(f"edge-ctl console deny --setup 7 --device {dev}")
    edge.run(f"edge-ctl console allow --setup 12 --device {dev}")
    try:
        listing = edge.run("edge-ctl console list --setup 12").stdout
        after = (tcp_open(t12, dev), tcp_open(t7, dev))
    finally:
        edge.run(f"edge-ctl console deny --setup 12 --device {dev}")
        edge.run(f"edge-ctl console allow --setup 7 --device {dev}")
    restored = (tcp_open(t12, dev), tcp_open(t7, dev))
    m = {"before": before, "after": after, "restored": restored, "list_setup12": listing.split("\n")}
    ok = before == (False, True) and after == (True, False) and restored == (False, True)
    write_protocol("E14e", "Regrouping moves the console", ok, m,
                   consequence="regrouping = switch port VLAN + console set on the edge (+ USB hub group)")
    assert ok, m


def test_e14_lab_console_refuses_foreign_device(lab):
    r = lab["ws-std"].run("lab console 12 sys1-dev3 </dev/null", check=False)
    assert r.returncode != 0 and "not in setup 12" in r.stderr


def test_e14_console_set_matches_inventory(lab):
    """edge-ctl console list (nft sets on Linux, filter rules on RouterOS) = consoles of the grouping."""
    out = lab["lab-edge"].run("edge-ctl console list").stdout.split("\n")
    got = sorted(line.split() for line in out if line.strip())
    want = sorted([str(s["id"]), ip, str(port)] for s in labcfg.CFG["setups"]
                  for _d, _n, ip, port in labcfg.vlab_cfg.consoles(labcfg.CFG, s["id"]))
    assert got == want
