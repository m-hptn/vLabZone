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


def _gen():
    import importlib.util
    spec = importlib.util.spec_from_file_location("gen", ROOT / "topology" / "gen.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("mode", ["vrf", "pbr", "hybrid"])
@pytest.mark.parametrize("view", ["network", "deployment", "logical"])
def test_viewer_layout_complete_and_without_overlaps(mode, view):
    """Every node of the lab has a position in every view, and nothing overlaps
    (boxes computed like the containerlab viewer does)."""
    import itertools

    import yaml as _yaml
    gen = _gen()
    cfg = _yaml.safe_load((ROOT / "config" / "setups.yaml").read_text())
    ann = gen.LAYOUTS[view](mode, cfg).annotations(None)
    lab_nodes = set(gen.build(mode, cfg)["topology"]["nodes"])
    placed = [n["id"] for n in ann["nodeAnnotations"]]
    assert set(placed) == lab_nodes and len(placed) == len(set(placed)), "every node exactly once"
    assert {n["groupId"] for n in ann["nodeAnnotations"]} <= {g["id"] for g in ann["groupStyleAnnotations"]}

    def overlap(a, b):
        return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]

    nodes, groups = gen.boxes(ann)
    member = {n["id"]: n["groupId"] for n in ann["nodeAnnotations"]}
    assert not [p for p in itertools.combinations(nodes, 2) if overlap(nodes[p[0]], nodes[p[1]])]
    assert not [p for p in itertools.combinations(groups, 2) if overlap(groups[p[0]], groups[p[1]])]
    assert not [(n, g) for n in nodes for g in groups if g != member[n] and overlap(nodes[n], groups[g])]
    for t in ann["freeTextAnnotations"]:
        box = (t["position"]["x"], t["position"]["y"], t["position"]["x"] + 7 * len(t["text"]), t["position"]["y"] + 20)
        assert not [n for n in nodes if overlap(box, nodes[n])], f"label {t['id']} covers a node"
