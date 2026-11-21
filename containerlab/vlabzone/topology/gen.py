#!/usr/bin/env python3
"""Generate the containerlab topologies from config/setups.yaml.

    topology/gen.py            write topology/vlabzone-{vrf,pbr,hybrid}.clab.yml, the view copies in
                               topology/views/ and a viewer layout next to each of them
                               (*.clab.yml.annotations.json, containerlab VS Code extension)
    topology/gen.py --check    exit 1 if a committed topology or view file is out of date

For every mode there is also a lab with the MikroTik as edge (EDGE_IMPL=routeros):
topology/vlabzone-<mode>-ros.clab.yml – identical except for `lab-edge` (RouterOS CHR, configured
over REST by drivers/edge_routeros.py, see docs/lab-routeros.md). Network view only.

Three views per lab (see VIEWS): network (switching), deployment (which real hardware runs
which node) and logical (workspace → firewall → edge → gateway → setup). The layouts are
always written in full; positions moved in the viewer are overwritten by the next
`make topology` / `make up`. Only the viewer settings (style preferences) of an existing
layout file are kept. Layout files are not committed; topologies and views are.

All labs share everything except the edge mode, the corporate WireGuard allowance and,
in PBR mode, the setup gateways gw-N. Generating them from one source keeps them from
drifting apart, and a new setup in setups.yaml shows up in every lab.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
MODES = ("vrf", "pbr", "hybrid")

DIAGRAM = {
    "vrf": """\
#   ws-std ─┐                                                                  ┌═ lab-sys1 ── sys1-dev1..4
#   ws-power┼─ corp-fw ─ UDP 51800-52099 ─ lab-edge ═ trunk (setup VLANs) ═ lab-agg ═┤
#   ws-ci  ─┘    │  └─ TCP 443/8443 ─ broker   │                              └═ lab-sys2 ── sys2-dev1..4
#   corp-svc ────┘                       └── lab-mgmt (10.198.0.0/24): edge, broker, agg, sys*
#
# VRF mode: the edge has a VRF, a VLAN interface and a WireGuard interface per setup.
# Setups = groups of devices on one system switch (config/setups.yaml).""",
    "pbr": """\
#   ws-std ─┐                                                                  ┌═ lab-sys1 ── sys1-dev1..4
#   ws-power┼─ corp-fw ─ UDP 51820 ─ lab-edge ═ trunk (VLAN 199) ═ lab-agg ═══════┤
#   ws-ci  ─┘    │  └─ TCP 443/8443 ─ broker  │                  ║║║           └═ lab-sys2 ── sys2-dev1..4
#   corp-svc ────┘                      │                  gw-12, gw-7, gw-5 (trunk: 199 + setup VLAN)
#                                       └── lab-mgmt (10.198.0.0/24): edge, broker, agg, sys*
#
# PBR mode: the edge has one wg0 and routes each tunnel IP (ip rule) to the
# gateway gw-N of the booked setup via the transfer VLAN 199.""",
    "hybrid": """\
#   ws-std ─┐                                                                  ┌═ lab-sys1 ── sys1-dev1..4
#   ws-power┼─ corp-fw ─ UDP 51820 ─ lab-edge ═ trunk (setup VLANs) ═ lab-agg ═══════┤
#   ws-ci  ─┘    │  └─ TCP 443/8443 ─ broker  │                                  └═ lab-sys2 ── sys2-dev1..4
#   corp-svc ────┘                      └── lab-mgmt (10.198.0.0/24): edge, broker, agg, sys*
#
# Hybrid mode: one wg0 (one port) like PBR, the setups in VRFs on the edge like VRF.
# One ip rule per setup points its tunnel prefix 10.250.N.0/24 into the setup VRF's table.""",
}


def node(image: str, **env) -> dict:
    n = {"image": f"hwpool/{image}:latest"}
    if env:
        n["env"] = {k: str(v) for k, v in env.items()}
    return n


def lab_name(mode: str, impl: str = "linux") -> str:
    return f"vlabzone-{mode}" + ("-ros" if impl == "routeros" else "")


def build(mode: str, cfg: dict, impl: str = "linux") -> dict:
    agg = cfg["aggregation"]
    nodes = {
        # corporate (simulated)
        "corp-fw": node("router", ROLE="corp-fw", EDGE_MODE=mode),   # allowance depends on the mode
        "corp-svc": node("device", NAME="corp-svc", SWITCH="corp", IP="10.20.20.10/24", GW="10.20.20.1"),
        "ws-std": node("workspace", WS_IP="10.20.10.11/24", WS_MODE="desktop"),
        "ws-power": node("workspace", WS_IP="10.20.10.12/24", WS_MODE="power"),
        "ws-ci": node("workspace", WS_IP="10.20.10.13/24", WS_MODE="alias"),
        # lab zone
        "lab-edge": node("edge", EDGE_MODE=mode),
        "broker": node("broker", EDGE_IMPL=impl, EDGE_MODE=mode),
        f"lab-{agg['name']}": node("router", ROLE="switch", SWITCH=agg["name"], EDGE_MODE=mode),
    }
    mgmt_members = ["lab-edge:eth3", "broker:eth2", f"lab-{agg['name']}:eth2"]
    links = [
        ("ws-std:eth1", "corp-fw:eth1"),
        ("ws-power:eth1", "corp-fw:eth2"),
        ("ws-ci:eth1", "corp-fw:eth3"),
        ("corp-svc:eth1", "corp-fw:eth4"),
        ("corp-fw:eth10", "lab-edge:eth1"),   # the one-time allowance: WireGuard
        ("corp-fw:eth11", "broker:eth1"),     # the one-time allowance: broker API
        ("lab-edge:eth2", f"lab-{agg['name']}:{agg['edge_port']}"),   # edge trunk
    ]
    wrong_gw = cfg["device_default_gateway"]
    for sw in cfg["switches"]:
        name = f"lab-{sw['name']}"
        nodes[name] = node("router", ROLE="switch", SWITCH=sw["name"], EDGE_MODE=mode)
        links.append((f"lab-{agg['name']}:{sw['agg_port']}", f"{name}:eth1"))   # uplink trunk
        mgmt_members.append(f"{name}:eth2")
        for d in sw["devices"]:
            nodes[d["name"]] = node("device", NAME=d["name"], SWITCH=sw["name"], IP=d["ip"], GW=wrong_gw)
            links.append((f"{d['name']}:eth1", f"{name}:{d['port']}"))
    # shared serial/USB appliances on the aggregation switch, appliance VLAN (docs/consoles-usb.md)
    apps = cfg.get("appliances", {})
    for kind, image in (("serial", "serial"), ("usb", "usbhub")):
        for a in apps.get(kind, []):
            nodes[a["name"]] = node(image, NAME=a["name"])
            links.append((f"{a['name']}:eth1", f"lab-{agg['name']}:{a['agg_port']}"))
    if mode == "pbr":
        for s in cfg["setups"]:
            gw = f"gw-{s['id']}"
            nodes[gw] = node("router", ROLE="gw", SETUP=s["id"])
            links.append((f"{gw}:eth1", f"lab-{agg['name']}:{s['pbr_gateway_port']}"))
    mgmt_ports = [f"eth{i}" for i in range(1, len(mgmt_members) + 1)]
    nodes["lab-mgmt"] = node("mgmt", PORTS=" ".join(mgmt_ports))
    links += [(member, f"lab-mgmt:{port}") for member, port in zip(mgmt_members, mgmt_ports)]

    defaults = {
        "kind": "linux",
        # no Docker management interface: nothing can bypass corp-fw
        "network-mode": "none",
        "binds": ["../config:/etc/vlab:ro"],
    }
    if impl == "routeros":
        # vrnetlab needs the management network for the CHR (ether1, REST). `network-mode: none`
        # moves from the defaults to every other node, so lab-edge is the only node on it.
        nodes["lab-edge"] = {"kind": "mikrotik_ros", "image": "vrnetlab/mikrotik_routeros:${ROUTEROS_VERSION:=7.24.6}"}
        for name, n in nodes.items():
            if name != "lab-edge":
                n["network-mode"] = "none"
        del defaults["network-mode"]
    return {
        "name": lab_name(mode, impl),
        "mgmt": {
            # always created by containerlab, but no node is attached (network-mode: none)
            "network": "vlab-mgmt",
            "ipv4-subnet": "${VLAB_MGMT_SUBNET:=172.31.254.0/24}",
            "ipv6-subnet": "${VLAB_MGMT_SUBNET6:=3fff:172:31:254::/64}",
        },
        "topology": {
            "defaults": defaults,
            "nodes": nodes,
            "links": [{"endpoints": list(pair)} for pair in links],
        },
    }


# --- viewer layouts (containerlab VS Code extension, format of 0.27) ----------------------
# Positions are the top-left corner of a node (about 100x100). The viewer sizes every group
# around its members (+40 px padding, at least 300x200). `boxes()` repeats that computation
# so the test suite can check that no node or group overlaps another.
#
# Views – one layout file per topology file:
#   network     topology/vlabzone-<mode>.clab.yml                 switching: edge → aggregation → system switches
#   deployment  topology/views/vlabzone-<mode>.deployment.clab.yml  which real hardware/platform runs which node
#   logical     topology/views/vlabzone-<mode>.logical.clab.yml     workspace → firewall → edge → gateway → setup
VIEWS = ("network", "deployment", "logical")
ROW = 140                         # vertical distance between nodes in a column
NODE = 100                        # node box used by the viewer
PAD, MIN_W, MIN_H = 40, 300, 200  # group padding and minimum group size used by the viewer

BLUE, AMBER, GREEN, PURPLE, GREY, RED = "#3b82f6", "#f59e0b", "#10b981", "#8b5cf6", "#6b7280", "#ef4444"


def icon(node_id: str) -> str:
    if node_id.startswith("ws-"):
        return "vm"
    if node_id.startswith(("lab-agg", "lab-sys", "lab-mgmt")):
        return "switch"
    if node_id.startswith(("lab-edge", "gw-")):
        return "router"
    if node_id.startswith(("nport", "ausb")):
        return "server"
    return {"corp-fw": "firewall", "corp-svc": "server", "broker": "controller"}.get(node_id, "client")


def appliance_names(cfg: dict) -> list[str]:
    apps = cfg.get("appliances", {})
    return [a["name"] for kind in ("serial", "usb") for a in apps.get(kind, [])]


class Canvas:
    def __init__(self) -> None:
        self.nodes: list[dict] = []
        self.groups: list[dict] = []
        self.texts: list[dict] = []

    def place(self, node_id: str, x: int, y: int, group: str) -> None:
        self.nodes.append({"id": node_id, "position": {"x": x, "y": y}, "icon": icon(node_id), "groupId": group})

    def group(self, gid: str, name: str, color: str) -> None:
        self.groups.append({"id": gid, "name": name, "level": "1", "backgroundColor": color, "backgroundOpacity": 10,
                            "borderColor": color, "borderWidth": 2, "borderStyle": "dashed", "borderRadius": 8,
                            "labelColor": color, "labelPosition": "top-left"})

    def text(self, tid: str, text: str, x: int, y: int, color: str = GREY, size: int = 12) -> None:
        self.texts.append({"id": tid, "text": text, "position": {"x": x, "y": y},
                           "fontSize": size, "fontColor": color, "fontWeight": "normal"})

    def annotations(self, viewer_settings: dict | None) -> dict:
        return {
            "freeTextAnnotations": self.texts,
            "freeShapeAnnotations": [],
            "trafficRateAnnotations": [],
            "scriptButtonAnnotations": [],
            "groupStyleAnnotations": self.groups,
            "networkNodeAnnotations": [],
            "nodeAnnotations": self.nodes,
            "edgeAnnotations": [],
            "aliasEndpointAnnotations": [],
            "viewerSettings": viewer_settings or {"nodeStyle": "boxed"},
        }


def boxes(annotations: dict) -> tuple[dict, dict]:
    """Node and group boxes (x0, y0, x1, y1) as the viewer computes them."""
    nb = {n["id"]: (n["position"]["x"], n["position"]["y"], n["position"]["x"] + NODE, n["position"]["y"] + NODE)
          for n in annotations["nodeAnnotations"]}
    gb = {}
    for g in annotations["groupStyleAnnotations"]:
        mem = [nb[n["id"]] for n in annotations["nodeAnnotations"] if n["groupId"] == g["id"]]
        x0, y0 = min(b[0] for b in mem) - PAD, min(b[1] for b in mem) - PAD
        x1, y1 = max(b[2] for b in mem) + PAD, max(b[3] for b in mem) + PAD
        gb[g["id"]] = (x0, y0, max(x1, x0 + MIN_W), max(y1, y0 + MIN_H))
    return nb, gb


def sys_label(cfg: dict, sw: dict) -> str:
    setups = [f"setup {s['id']}: " + ", ".join(d.split("-")[-1] for d in s["devices"])
              for s in cfg["setups"] if s["switch"] == sw["name"]]
    return " · ".join(setups) or "no setup"


def systems(c: Canvas, cfg: dict, x_sw: int, x_dev: int, group_name) -> list[int]:
    """System switches with their devices, one block below the other. Returns the switch rows."""
    y, rows = 0, []
    for sw in cfg["switches"]:
        gid = f"sys-{sw['name']}"
        for i, d in enumerate(sw["devices"]):
            c.place(d["name"], x_dev, y + i * ROW, gid)
        row = y + (len(sw["devices"]) - 1) * ROW // 2
        c.place(f"lab-{sw['name']}", x_sw, row, gid)
        c.group(gid, group_name(sw), GREEN)
        rows.append(row)
        y += len(sw["devices"]) * ROW + 100
    return rows


def view_network(mode: str, cfg: dict) -> Canvas:
    c = Canvas()
    rows = systems(c, cfg, 1080, 1300, lambda sw: f"System switch {sw['name']} – {sys_label(cfg, sw)}")
    mid = (rows[0] + rows[-1]) // 2
    c.group("corporate", "Corporate (simulated)", BLUE)
    for i, ws in enumerate(["ws-std", "ws-power", "ws-ci"]):
        c.place(ws, 0, mid - ROW * 3 // 2 + i * ROW, "corporate")
    c.place("corp-svc", 0, mid + ROW * 3 // 2 + 20, "corporate")
    c.place("corp-fw", 220, mid, "corporate")
    c.group("lab-core", "Lab zone: edge, aggregation, management", AMBER)
    c.place("lab-edge", 460, mid, "lab-core")
    c.place("broker", 460, mid + ROW * 2, "lab-core")
    c.place(f"lab-{cfg['aggregation']['name']}", 700, mid, "lab-core")
    c.place("lab-mgmt", 700, mid + ROW * 2, "lab-core")
    if mode == "pbr":
        for i, s in enumerate(cfg["setups"]):
            c.place(f"gw-{s['id']}", 580 + i * 120, mid - ROW * 2 + 20, "lab-core")
    apps = appliance_names(cfg)
    if apps:
        c.group("appliances", "Serial/USB appliances – VLAN 197, via the edge", PURPLE)
        for i, n in enumerate(apps):
            c.place(n, 460 + i * 240, mid + ROW * 3 + 60, "appliances")
    c.groups = c.groups[-3:] + c.groups[:-3] if apps else c.groups[-2:] + c.groups[:-2]
    return c


def view_deployment(mode: str, cfg: dict) -> Canvas:
    """Which real hardware or platform runs which lab node (docs/diagrams/device_arc.drawio)."""
    c = Canvas()
    rows = systems(c, cfg, 1420, 1640,
                   lambda sw: f"System rack {sw['name']}: Lancom system switch + devices ({sys_label(cfg, sw)})")
    mid = (rows[0] + rows[-1]) // 2
    c.group("vsphere", "vSphere cluster – workspaces (VDI)", BLUE)
    for i, ws in enumerate(["ws-std", "ws-power", "ws-ci"]):
        c.place(ws, 0, mid - ROW + i * ROW, "vsphere")
    c.group("corporate", "Corporate network", GREY)
    c.place("corp-fw", 340, mid, "corporate")
    c.place("corp-svc", 340, mid + ROW * 2, "corporate")
    c.group("mikrotik", "MikroTik router – lab edge", RED)
    c.place("lab-edge", 680, mid - ROW * 2, "mikrotik")
    c.group("proxmox", "Proxmox cluster – broker, setup gateways", PURPLE)
    on_proxmox = ["broker"] + ([f"gw-{s['id']}" for s in cfg["setups"]] if mode == "pbr" else [])
    for i, n in enumerate(on_proxmox):
        c.place(n, 680 + (i % 2) * 140, mid + 40 + (i // 2) * ROW, "proxmox")
    c.group("aggregation", "Lancom aggregation switch (+ management VLAN)", AMBER)
    c.place(f"lab-{cfg['aggregation']['name']}", 1080, mid, "aggregation")
    c.place("lab-mgmt", 1080, mid + ROW * 2, "aggregation")
    apps = appliance_names(cfg)
    if apps:
        c.group("appliances", "Moxa NPort 5600 (serial) · Digi AnywhereUSB 24 Plus (USB)", GREY)
        for i, n in enumerate(apps):
            c.place(n, 960 + i * 160, mid + ROW * 3 + 60, "appliances")
    c.groups = c.groups[len(cfg["switches"]):] + c.groups[:len(cfg["switches"])]
    return c


def view_logical(mode: str, cfg: dict) -> Canvas:
    """The access path: workspace → corporate firewall → edge → setup gateway → setup."""
    c = Canvas()
    X_WS, X_FW, X_EDGE, X_GW, X_DEV = 0, 300, 640, 960, 1260
    y = 0
    lanes = []
    for s in cfg["setups"]:
        gid = f"setup-{s['id']}"
        n = len(s["devices"])
        for i, d in enumerate(s["devices"]):
            c.place(d, X_DEV, y + i * ROW, gid)
        row = y + (n - 1) * ROW // 2
        if mode == "pbr":
            c.place(f"gw-{s['id']}", X_GW, row, gid)
            gw = f"gateway gw-{s['id']} · 192.168.1.254"
        else:
            gw = f"gateway: lab-edge vrf-s{s['id']} · 192.168.1.254"
            c.text(f"gw-label-{s['id']}", f"gateway: lab-edge vrf-s{s['id']}", X_GW - 60, row + 40, AMBER)
        c.group(gid, f"Setup {s['id']} – VLAN {s['vlan']} on {s['switch']} – {gw}", GREEN)
        lanes.append(row)
        y += max(n, 2) * ROW + 120
    ungrouped = [d["name"] for sw in cfg["switches"] for d in sw["devices"]
                 if not any(d["name"] in s["devices"] for s in cfg["setups"])]
    if ungrouped:
        for i, d in enumerate(ungrouped):
            c.place(d, X_DEV + (i % 2) * 140, y + (i // 2) * ROW, "ungrouped")
        c.group("ungrouped", "Ungrouped devices – access port in no VLAN, unreachable", GREY)
        y += ((len(ungrouped) + 1) // 2) * ROW + 120
    mid = (lanes[0] + lanes[-1]) // 2

    c.group("corporate", "Corporate: workspaces, firewall (one-time allowance)", BLUE)
    for i, ws in enumerate(["ws-std", "ws-power", "ws-ci"]):
        c.place(ws, X_WS, mid - ROW + i * ROW, "corporate")
    c.place("corp-svc", X_WS, mid + ROW * 2 + 20, "corporate")
    c.place("corp-fw", X_FW, mid, "corporate")
    port = {"vrf": "UDP 51800+N per setup", "pbr": "UDP 51820, ip rule per booking",
            "hybrid": "UDP 51820, ip rule per booking into the setup VRF"}[mode]
    c.group("edge", f"Lab edge – WireGuard ({port})", RED)
    c.place("lab-edge", X_EDGE, mid, "edge")

    c.group("control", "Control plane", PURPLE)
    c.place("broker", X_FW, y + 40, "control")
    c.group("transport", "Transport – switches carry the VLANs, not part of the logic", GREY)
    agg = cfg["aggregation"]["name"]
    for i, n in enumerate([f"lab-{agg}"] + [f"lab-{sw['name']}" for sw in cfg["switches"]] + ["lab-mgmt"]):
        c.place(n, X_EDGE + i * 160, y + 40, "transport")
    apps = appliance_names(cfg)
    if apps:
        c.group("appliances", "Shared serial/USB appliances – per setup only its own ports", PURPLE)
        for i, n in enumerate(apps):
            c.place(n, X_EDGE + i * 160, y + 40 + ROW + 120, "appliances")
    order = ["corporate", "edge"]
    c.groups.sort(key=lambda g: order.index(g["id"]) if g["id"] in order else len(order))
    return c


LAYOUTS = {"network": view_network, "deployment": view_deployment, "logical": view_logical}


def topo_path(mode: str, view: str, impl: str = "linux") -> Path:
    if view == "network":
        return ROOT / "topology" / f"{lab_name(mode, impl)}.clab.yml"
    return ROOT / "topology" / "views" / f"vlabzone-{mode}.{view}.clab.yml"


def write_layout(mode: str, view: str, cfg: dict, impl: str = "linux") -> None:
    path = Path(str(topo_path(mode, view, impl)) + ".annotations.json")
    settings = None
    if path.exists():
        try:
            settings = json.loads(path.read_text()).get("viewerSettings")
        except (ValueError, AttributeError):
            settings = None
    text = json.dumps(LAYOUTS[view](mode, cfg).annotations(settings), indent=2, ensure_ascii=False) + "\n"
    if not path.exists() or path.read_text() != text:
        path.write_text(text)
        print(f"wrote {path.relative_to(ROOT)}")


def render(mode: str, cfg: dict, view: str = "network", impl: str = "linux") -> str:
    topo = build(mode, cfg, impl)
    if view == "network":
        edge = ("lab-edge = MikroTik RouterOS CHR (EDGE_IMPL=routeros), configured by drivers/edge_routeros.py,\n"
                "# docs/lab-routeros.md\n#\n" if impl == "routeros" else "")
        header = (
            f"# GENERATED by topology/gen.py from config/setups.yaml – do not edit, run `make topology`.\n"
            f"#\n# vLabZone lab, edge mode {mode.upper()} (docs/concept.md §4, addresses §4.3, nodes: docs/lab-{mode}.md)\n#\n"
            + (f"# {edge}" if edge else "") +
            f"{DIAGRAM[mode]}\n"
        )
    else:
        # same lab (same name) – only the viewer layout next to this file differs
        topo["topology"]["defaults"]["binds"] = ["../../config:/etc/vlab:ro"]
        header = (
            f"# GENERATED by topology/gen.py – VIEW ONLY, DO NOT DEPLOY.\n"
            f"#\n# {view.capitalize()} view of lab vlabzone-{mode}: identical to topology/vlabzone-{mode}.clab.yml,\n"
            f"# it only exists so the containerlab viewer can show a second layout (next to this file).\n"
            f"# Deploy with `make up EDGE_MODE={mode}`, not with this file.\n"
        )
    body = yaml.safe_dump(topo, sort_keys=False, width=200, default_flow_style=False)
    return header + body


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="only check that the files are up to date")
    args = ap.parse_args()

    cfg = yaml.safe_load((ROOT / "config" / "setups.yaml").read_text())
    stale = []
    variants = [(m, v, "linux") for m in MODES for v in VIEWS] + [(m, "network", "routeros") for m in MODES]
    for mode, view, impl in variants:
        path = topo_path(mode, view, impl)
        text = render(mode, cfg, view, impl)
        if args.check:
            if not path.exists() or path.read_text() != text:
                stale.append(str(path.relative_to(ROOT)))
            continue
        path.parent.mkdir(exist_ok=True)
        if not path.exists() or path.read_text() != text:
            path.write_text(text)
            print(f"wrote {path.relative_to(ROOT)}")
        write_layout(mode, view, cfg, impl)
    if stale:
        print(f"out of date: {', '.join(stale)} – run `make topology`", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
