"""E05 – Are there ways around the architecture?

From the workspaces: edge, switch, broker and devices directly (without tunnel),
also via the management network. From a setup: corporate network, other setup,
management, internet. Everything must fail.
"""
import pytest

from tests.conftest import ALICE, CI, LAB_NODES, ident
from tests.lib import labcfg
from tests.lib.capture import capture
from tests.lib.results import write_protocol

pytestmark = pytest.mark.stage1


def tcp_open(node, host: str, port: int, timeout: int = 3) -> bool:
    return node.run(f"timeout {timeout} bash -c '</dev/tcp/{host}/{port}'", check=False).returncode == 0


def ping_ok(node, host: str) -> bool:
    return node.run(f"ping -c1 -W2 {host}", check=False).returncode == 0


@pytest.mark.parametrize("name", LAB_NODES)
def test_e05_no_management_interface(lab, name):
    assert lab[name].run("test -e /sys/class/net/eth0", check=False).returncode != 0, \
        f"{name} has eth0 – Docker management network would bypass corp-fw"


def test_e05_workspace_without_tunnel(lab):
    ws = lab["ws-std"]
    attempts = {
        "device 192.168.1.20:80": tcp_open(ws, "192.168.1.20", 80),
        "edge 10.20.99.2:22": tcp_open(ws, "10.20.99.2", 22),
        "edge ping 10.20.99.2": ping_ok(ws, "10.20.99.2"),
        "broker 10.20.98.2:22": tcp_open(ws, "10.20.98.2", 22),
        "edge mgmt 10.198.0.2": ping_ok(ws, "10.198.0.2"),
        "switch mgmt 10.198.0.3": ping_ok(ws, "10.198.0.3"),
        "tunnel gateway 10.250.0.1": ping_ok(ws, "10.250.0.1"),
    }
    # sanity: the allowed paths and the corporate network do work
    assert tcp_open(ws, "10.20.98.2", 8443), "broker 8443 should be reachable"
    assert tcp_open(ws, "10.20.20.10", 80), "corp-svc should be reachable"

    open_paths = [k for k, ok in attempts.items() if ok]
    write_protocol("E05a", "No bypass from workspaces", not open_paths, attempts)
    assert not open_paths, f"bypass possible: {open_paths}"


def test_e05_setup_cannot_reach_out(lab):
    """Even with the edge as default gateway, a device cannot open connections outwards."""
    dev = lab[labcfg.device_at(12, "192.168.1.20")]
    dev.run("ip route replace default via 192.168.1.254")
    try:
        attempts = {
            "workspace ping 10.20.10.11": ping_ok(dev, "10.20.10.11"),
            "corp-svc 10.20.20.10:80": tcp_open(dev, "10.20.20.10", 80),
            "corp-fw 10.20.99.1": ping_ok(dev, "10.20.99.1"),
            "broker mgmt 10.198.0.1": ping_ok(dev, "10.198.0.1"),
            "edge mgmt 10.198.0.2:22": tcp_open(dev, "10.198.0.2", 22),
            "tunnel gateway 10.250.0.1": ping_ok(dev, "10.250.0.1"),
            "edge transfer 10.199.0.1": ping_ok(dev, "10.199.0.1"),
            "other setup's gateway 10.199.0.7": ping_ok(dev, "10.199.0.7"),
            "internet 1.1.1.1:443": tcp_open(dev, "1.1.1.1", 443),
        }
    finally:
        dev.run("ip route replace default via 192.168.1.1")
    open_paths = [k for k, ok in attempts.items() if ok]
    write_protocol("E05b", "No new connections out of a setup", not open_paths, attempts)
    assert not open_paths, f"setup can reach: {open_paths}"


def test_e05_setups_isolated_on_layer2(lab):
    """ARP/broadcast from setup 12 never reaches setup 7's access ports – on the SAME switch."""
    dev12 = labcfg.device_at(12, "192.168.1.20")
    dev7, other7 = labcfg.CFG["setups"][1]["devices"]          # setup 7: both on sys1
    port7 = labcfg.device(dev7)["port"]
    with capture(lab["lab-sys1"], port7, "arp", name="e05-sys1-setup7-port") as cap:
        lab[dev12].run("ping -c2 -W1 192.168.1.99", check=False)
    leaked = [l for l in cap.lines if "192.168.1.99" in l]
    # positive control: a broadcast inside setup 7 is seen on the same port
    with capture(lab["lab-sys1"], port7, "arp", name="e05-sys1-setup7-port-control") as control:
        lab[other7].run("ping -c2 -W1 192.168.1.98", check=False)
    assert any("192.168.1.98" in l for l in control.lines), "capture sees no ARP – test would be meaningless"
    assert not leaked, f"setup 12 broadcast seen in setup 7: {leaked}"


def test_e05_ungrouped_devices_unreachable(lab, tunnel):
    """Devices in no setup have an access port without any VLAN: nobody reaches them."""
    t = tunnel("ws-ci", CI, 5)                                  # setup 5 lives on the same switch
    ungrouped = [d for sw in labcfg.CFG["switches"] for d in sw["devices"] if not labcfg.setup_of(d["name"])]
    answers = {}
    for d in ungrouped:
        ip = d["ip"].split("/")[0]
        answers[d["name"]] = ident(t.curl(f"http://{ip}", timeout=2))
    # where an ungrouped device shares its IP with a grouped one, only the grouped one may answer
    leaked = {n: a for n, a in answers.items() if a and a["device"] == n}
    write_protocol("E05c", "Ungrouped devices are cut off", not leaked,
                   {n: (a["device"] if a else None) for n, a in answers.items()})
    assert ungrouped, "config has no ungrouped device – test would be meaningless"
    assert not leaked, f"ungrouped devices answered: {list(leaked)}"
