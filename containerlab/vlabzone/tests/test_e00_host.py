"""Stage 0: the host and the images are ready (no running lab needed)."""
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
VERSION = (ROOT / "VERSION").read_text().strip()
IMAGES = ["base", "router", "edge", "workspace", "device", "serial", "mgmt", "broker"]

pytestmark = pytest.mark.stage0


@pytest.mark.parametrize("module", ["wireguard", "vrf", "8021q", "bridge"])
def test_kernel_module_loaded(module):
    assert Path(f"/sys/module/{module}").is_dir(), f"run: sudo modprobe {module}"


@pytest.mark.parametrize("image", IMAGES)
def test_image_built(image):
    res = subprocess.run(["docker", "image", "inspect", f"hwpool/{image}:{VERSION}"], capture_output=True)
    assert res.returncode == 0, f"hwpool/{image}:{VERSION} missing – run make build"


def test_setups_config_consistent(setups):
    vlans = [s["vlan"] for s in setups["setups"]]
    assert len(vlans) == len(set(vlans)), "VLAN IDs must be unique"
    ports = [d["port"] for s in setups["setups"] for d in s["devices"]]
    assert len(ports) == len(set(ports)), "switch ports must be unique"


def test_routeros_image(edge_impl):
    if edge_impl != "routeros":
        pytest.skip("EDGE_IMPL=linux")
    res = subprocess.run(["docker", "image", "ls", "-q", "vrnetlab/mikrotik_routeros"], capture_output=True, text=True)
    assert res.stdout.strip(), "RouterOS image missing – run make build-routeros"
