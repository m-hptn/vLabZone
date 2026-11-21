"""E26 – USB devices on the shared USB hub (Digi AnywhereUSB 24 Plus, docs/consoles-usb.md).

The AnywhereUSB has ONE TCP port (18574) for all its USB ports, so the edge can't tell the ports
apart: every setup may reach the hub. Which ports a client gets is decided by the hub's groups –
group N = the USB ports of setup N's devices, members = the clients of setup N's bookings (on the
real hub: client IDs, here the tunnel address [sim]). The broker adds a booking's client and
removes it on revoke (hub-ctl). Stand-in protocol: USB/IP (ausb1, images/usbhub).

Expected:
  - without membership the hub lists nothing; with it, only the own setup's ports
  - an own port can be imported (product string of the cabled device); a foreign one can't
  - revoking the membership refuses new imports and ends a running session within ~1 s
  - optional: the kernel `usbip attach` (needs vhci-hcd on the host) shows the device
"""
import json
import time
from pathlib import Path

import pytest

from tests.conftest import ALICE, BOB
from tests.lib import labcfg
from tests.lib.results import write_protocol
from tests.lib.wg import tunnel_ip

pytestmark = pytest.mark.stage2

APP_NET = labcfg.CFG["appliances"]["net"]
ROUTES = ("192.168.1.0/24", APP_NET)
HUB = labcfg.CFG["appliances"]["usb"][0]
HUB_ADDR = f"{HUB['ip']} {HUB['tcp_port']}"


def busid(device: str) -> str:
    return f"1-{labcfg.device(device)['usb']['port']}"


@pytest.fixture
def hub(lab):
    """hub.add(ip, setup) / hub.remove(ip): group membership on ausb1, undone after the test."""
    node, added = lab[HUB["name"]], []

    class Hub:
        def add(self, ip, setup):
            node.run(f"hub-ctl client add {ip} --group {setup}")
            added.append(ip)

        def remove(self, ip):
            node.run(f"hub-ctl client del {ip}")

    yield Hub()
    for ip in added:
        node.run(f"hub-ctl client del {ip}", check=False)


def usb_list(t) -> list[dict] | None:
    r = t.run(f"usbip-client list {HUB_ADDR}", check=False)
    if r.returncode != 0:
        return None
    return [json.loads(line) for line in r.stdout.splitlines() if line.strip()]


def usb_import(t, bid: str) -> dict | None:
    r = t.run(f"usbip-client import {HUB_ADDR} {bid}", check=False)
    return json.loads(r.stdout) if r.returncode == 0 else None


def test_e26_list_only_own_ports(lab, tunnel, hub):
    t = tunnel("ws-std", ALICE, 12, routes=ROUTES)
    before = usb_list(t)
    hub.add(tunnel_ip(ALICE, 12), 12)
    after = usb_list(t)
    got = [d["busid"] for d in after or []]
    want = [busid(d) for d in labcfg.setup(12)["devices"] if "usb" in labcfg.device(d)]
    ok = before == [] and got == want
    write_protocol("E26a", "USB hub lists only the own setup's ports", ok,
                   {"without_membership": before, "with_membership": got, "expected": want})
    assert ok, (before, got, want)


def test_e26_import_own_not_foreign(lab, tunnel, hub):
    t = tunnel("ws-std", ALICE, 12, routes=ROUTES)
    hub.add(tunnel_ip(ALICE, 12), 12)
    own = usb_import(t, busid("sys1-dev1"))
    foreign = usb_import(t, busid("sys1-dev3"))             # setup 7's device, same hub
    t_bob = tunnel("ws-power", BOB, 12, netns="setup12", routes=ROUTES)
    not_member = usb_import(t_bob, busid("sys1-dev1"))      # booked setup 12, but not in the group
    ok = bool(own) and own.get("product_string") == "sys1-dev1 USB" and foreign is None and not_member is None
    write_protocol("E26b", "Import of own / foreign USB ports", ok,
                   {"own": own, "foreign": foreign, "booked_but_not_member": not_member},
                   consequence="USB access = tunnel (edge) AND group membership (hub): the broker must keep both")
    assert ok, (own, foreign, not_member)


def test_e26_revoke_ends_session(lab, tunnel, hub):
    """A held session (control transfers every 0.2 s) ends when the client leaves the group."""
    t = tunnel("ws-std", ALICE, 12, routes=ROUTES)
    ip = tunnel_ip(ALICE, 12)
    hub.add(ip, 12)
    hold = f"""
import sys, time
sys.path.insert(0, "/usr/local/lib/vlab")
import socket, vlab_usbip as u
s = socket.create_connection(("{HUB['ip']}", {HUB['tcp_port']}), timeout=3)
s.sendall(u.OP_HDR.pack(u.VERSION, u.OP_REQ_IMPORT, 0) + b"{busid('sys1-dev1')}".ljust(32, b"\\0"))
assert u.OP_HDR.unpack(u.recv_exact(s, 8))[2] == 0
u.recv_exact(s, u.DEV.size)
t0, seq = time.time(), 0
try:
    while time.time() - t0 < 10:
        seq += 1
        u.control_in(s, seq, u.get_descriptor(1, 0, 18), 18)
        time.sleep(0.2)
    print("still open after 10 s")
except Exception:
    print(f"ended after {{time.time() - t0:.1f}} s")
"""
    import threading
    result = {}
    th = threading.Thread(target=lambda: result.update(
        out=t.run(f"python3 - <<'EOF'\n{hold}\nEOF", check=False, timeout=30).stdout.strip()))
    th.start()
    time.sleep(2)
    t_revoke = time.monotonic()
    hub.remove(ip)
    th.join()
    after = usb_import(t, busid("sys1-dev1"))
    ended = result.get("out", "").startswith("ended after")
    secs = round(float(result["out"].split()[2]) - 2, 1) if ended else None
    ok = ended and secs is not None and secs < 2.5 and after is None
    write_protocol("E26c", "Revoke ends a running USB session", ok,
                   {"session": result.get("out"), "seconds_after_revoke": secs, "import_after_revoke": after,
                    "revoke_at": round(t_revoke, 1)})
    assert ok, (result, after)


def test_e26_hub_reachable_but_empty_for_other_setups(lab, tunnel, hub):
    """Setup 5 reaches the hub's port (the network can't split it) but sees none of setup 12's ports."""
    t5 = tunnel("ws-std", ALICE, 5, routes=ROUTES)
    hub.add(tunnel_ip(ALICE, 12), 12)                       # someone else's booking in setup 12
    listing = usb_list(t5)
    write_protocol("E26d", "Other setups see the hub, not the ports", listing == [], {"setup5_list": listing})
    assert listing == [], listing


@pytest.mark.skipif(not Path("/sys/module/vhci_hcd").exists(),
                    reason="vhci-hcd not loaded on the host (sudo modprobe vhci-hcd); the attached device "
                           "would be visible host-wide")
def test_e26_kernel_attach(lab, tunnel, hub):
    t = tunnel("ws-std", ALICE, 12, routes=ROUTES)
    hub.add(tunnel_ip(ALICE, 12), 12)
    ws = lab["ws-std"]
    r = t.run(f"usbip --tcp-port {HUB['tcp_port']} attach -r {HUB['ip']} -b {busid('sys1-dev1')}", check=False)
    try:
        time.sleep(2)
        products = ws.run("cat /sys/bus/usb/devices/*/product 2>/dev/null", check=False).stdout
    finally:
        port = ws.run("usbip port 2>/dev/null | awk '/^Port/ {print $2}' | tr -d :", check=False).stdout.split()
        for p in port:
            ws.run(f"usbip detach -p {p}", check=False)
    ok = r.returncode == 0 and "sys1-dev1 USB" in products
    write_protocol("E26e", "Kernel usbip attach", ok, {"attach": r.stderr.strip(), "products": products.split("\n")})
    assert ok, (r.stderr, products)
