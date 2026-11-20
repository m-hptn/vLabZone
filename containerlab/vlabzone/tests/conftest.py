import os
import shutil
from pathlib import Path

import pytest
import yaml

from tests.lib.node import Node

ROOT = Path(__file__).resolve().parents[1]
LAB = "hwpool"


def pytest_configure(config):
    for stage in range(5):
        config.addinivalue_line("markers", f"stage{stage}: experiments of stage {stage}")
    config.addinivalue_line("markers", "routeros: needs the RouterOS CHR edge (KVM)")


@pytest.fixture(scope="session")
def edge_impl() -> str:
    return os.environ.get("EDGE_IMPL", "linux")


@pytest.fixture(scope="session")
def edge_mode() -> str:
    return os.environ.get("EDGE_MODE", "vrf")


@pytest.fixture(scope="session")
def setups() -> dict:
    return yaml.safe_load((ROOT / "config" / "setups.yaml").read_text())


@pytest.fixture
def node():
    """node('ws-std') -> Node; skips the test if the lab is not deployed."""
    def _get(name: str) -> Node:
        n = Node(LAB, name)
        if not n.is_running():
            pytest.skip(f"lab node {n.container} not running (make up)")
        return n
    return _get


@pytest.fixture(scope="session", autouse=True)
def _docker_available():
    if shutil.which("docker") is None:
        pytest.exit("docker CLI not found", returncode=2)
