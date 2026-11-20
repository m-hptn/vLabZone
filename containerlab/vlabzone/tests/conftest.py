import json
import os
import shutil
from pathlib import Path

import pytest
import yaml

from tests.lib import labcfg, wg
from tests.lib.node import Node, RouterOSEdge

ROOT = Path(__file__).resolve().parents[1]
# One lab per edge mode and edge implementation (topology/gen.py):
#   vlabzone-{vrf,pbr,hybrid} (Linux edge), vlabzone-{vrf,pbr,hybrid}-ros (MikroTik edge)
EDGE_MODE = os.environ.get("EDGE_MODE", "vrf")
EDGE_IMPL = os.environ.get("EDGE_IMPL", "linux")
LAB = f"vlabzone-{EDGE_MODE}" + ("-ros" if EDGE_IMPL == "routeros" else "")
TOPOLOGY = yaml.safe_load((ROOT / "topology" / f"{LAB}.clab.yml").read_text())
LAB_NODES = sorted(TOPOLOGY["topology"]["nodes"])

# Test users (config/users.yaml): tunnel IP = 10.250.<setup>.<user id> (docs/tunnel-addresses.md)
ALICE, BOB, CI = 1, 2, 3


def pytest_configure(config):
    for stage in range(5):
        config.addinivalue_line("markers", f"stage{stage}: experiments of stage {stage}")
    config.addinivalue_line("markers", "routeros: needs the RouterOS CHR edge (KVM)")


@pytest.fixture(scope="session", autouse=True)
def _docker_available():
    if shutil.which("docker") is None:
        pytest.exit("docker CLI not found", returncode=2)


@pytest.fixture(scope="session")
def edge_impl() -> str:
    return EDGE_IMPL


@pytest.fixture(scope="session")
def edge_mode() -> str:
    return EDGE_MODE


@pytest.fixture(scope="session")
def cfg() -> dict:
    """config/setups.yaml (inventory + initial grouping)"""
    return labcfg.CFG


@pytest.fixture(scope="session")
def lab() -> dict[str, Node]:
    """All nodes of the deployed lab; skips the test if the lab is not up."""
    nodes = {name: Node(LAB, name) for name in LAB_NODES}
    if EDGE_IMPL == "routeros":
        nodes["lab-edge"] = RouterOSEdge(LAB, "lab-edge", mode=EDGE_MODE)
    missing = [n.container for n in nodes.values() if not n.is_running()]
    if missing:
        pytest.skip(f"lab {LAB} not deployed (make up EDGE_MODE={EDGE_MODE} EDGE_IMPL={EDGE_IMPL}) – "
                    f"missing {', '.join(missing[:3])}")
    not_ready = [n.name for n in nodes.values()
                 if (not n.ready() if isinstance(n, RouterOSEdge)
                     else n.run("test -f /run/vlab-ready", check=False).returncode)]
    if not_ready:
        pytest.fail(f"lab nodes not ready: {not_ready} (scripts/wait-ready.sh {LAB})")
    return nodes


@pytest.fixture
def node(lab):
    """node('ws-std') -> Node"""
    return lab.__getitem__


@pytest.fixture
def tunnel(lab):
    """Factory: tunnel('ws-std', ALICE, 12, netns=None, register=True, ip=None) -> wg.Tunnel.
    Every tunnel is torn down after the test (interface + edge peer)."""
    created: list[wg.Tunnel] = []

    def _make(ws: str, user: int, setup: int, **kw) -> wg.Tunnel:
        t = wg.connect(lab[ws], lab["lab-edge"], user, setup, **kw)
        created.append(t)
        return t

    yield _make
    for t in reversed(created):
        t.close()


@pytest.fixture
def nat(lab):
    """nat(setup, on: bool) – switch the MASQUERADE into a setup where it lives:
    on the edge in VRF mode, on the setup gateway gw-N in PBR mode."""
    def _set(setup: int, on: bool) -> None:
        state = "on" if on else "off"
        if EDGE_MODE != "pbr":
            lab["lab-edge"].run(f"edge-ctl nat {state} --setup {setup}")
        else:
            lab[f"gw-{setup}"].run(f"gw-ctl nat {state}")
    return _set


@pytest.fixture
def flush_arp(lab):
    """flush_arp(setup) – forget the neighbour cache of a setup's gateway (edge VRF or gw-N),
    so the next access ARPs again. Needed after moving devices between setups."""
    def _flush(setup: int) -> None:
        vlan = next(s["vlan"] for s in labcfg.CFG["setups"] if s["id"] == setup)
        if EDGE_MODE != "pbr":
            lab["lab-edge"].run(f"edge-ctl neigh flush --setup {setup}")
        else:
            lab[f"gw-{setup}"].run(f"ip neigh flush dev eth1.{vlan}")
    return _flush


def in_setup(answer: dict | None, setup: int, ip: str = "192.168.1.20") -> bool:
    """True if <answer> came from the device that owns <ip> in <setup> (initial grouping)."""
    return bool(answer) and answer["device"] == labcfg.device_at(setup, ip)


def ident(result) -> dict | None:
    """Parse the JSON identity page of a device, None if there was no answer."""
    if result.returncode != 0 or not result.stdout.strip():
        return None
    return json.loads(result.stdout)
