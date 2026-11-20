"""Stage 0: the host and the images are ready (no running lab needed)."""
import subprocess
import sys
from pathlib import Path

from tests.lib import labcfg

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


def test_setups_config_consistent(cfg):
    setups = cfg["setups"]
    for key in ("id", "vlan"):
        values = [s[key] for s in setups]
        assert len(values) == len(set(values)), f"setup {key}s must be unique"
    for sw in cfg["switches"]:
        ports = [d["port"] for d in sw["devices"]]
        assert len(ports) == len(set(ports)), f"{sw['name']}: switch ports must be unique"
    switch_of = {d["name"]: sw["name"] for sw in cfg["switches"] for d in sw["devices"]}
    grouped = [d for s in setups for d in s["devices"]]
    assert len(grouped) == len(set(grouped)), "a device can be in one setup only"
    for s in setups:
        for d in s["devices"]:
            assert switch_of.get(d) == s["switch"], f"setup {s['id']}: {d} is not on switch {s['switch']}"
        ips = [labcfg.device(d)["ip"] for d in s["devices"]]
        # same IPs on one switch are fine – but never inside one setup (the broker must refuse it)
        assert len(ips) == len(set(ips)), f"setup {s['id']}: duplicate IP {ips}"


def test_topologies_up_to_date():
    """topology/vlabzone-*.clab.yml must match config/setups.yaml (run `make topology`)."""
    res = subprocess.run([sys.executable, str(ROOT / "topology" / "gen.py"), "--check"], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr


def test_routeros_image(edge_impl):
    if edge_impl != "routeros":
        pytest.skip("EDGE_IMPL=linux")
    res = subprocess.run(["docker", "image", "ls", "-q", "vrnetlab/mikrotik_routeros"], capture_output=True, text=True)
    assert res.stdout.strip(), "RouterOS image missing – run make build-routeros"
