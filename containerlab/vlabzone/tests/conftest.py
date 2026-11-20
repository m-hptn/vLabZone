import json
import os
import shutil
from pathlib import Path

import pytest
import yaml

from tests.lib import wg
from tests.lib.node import Node

ROOT = Path(__file__).resolve().parents[1]
LAB = "hwpool"
TOPOLOGY = yaml.safe_load((ROOT / "topology" / "hwpool.clab.yml").read_text())
LAB_NODES = sorted(TOPOLOGY["topology"]["nodes"])

# Test users (config/users.yaml): tunnel IP = 10.250.<user id>.<setup>
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
    return os.environ.get("EDGE_IMPL", "linux")


@pytest.fixture(scope="session")
def edge_mode() -> str:
    return os.environ.get("EDGE_MODE", "vrf")


@pytest.fixture(scope="session")
def setups() -> dict:
    return yaml.safe_load((ROOT / "config" / "setups.yaml").read_text())


@pytest.fixture(scope="session")
def lab() -> dict[str, Node]:
    """All nodes of the deployed lab; skips the test if the lab is not up."""
    nodes = {name: Node(LAB, name) for name in LAB_NODES}
    missing = [n.container for n in nodes.values() if not n.is_running()]
    if missing:
        pytest.skip(f"lab not deployed (make up) – missing {', '.join(missing[:3])}")
    not_ready = [n.name for n in nodes.values() if n.run("test -f /run/vlab-ready", check=False).returncode]
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


def ident(result) -> dict | None:
    """Parse the JSON identity page of a device, None if there was no answer."""
    if result.returncode != 0 or not result.stdout.strip():
        return None
    return json.loads(result.stdout)
