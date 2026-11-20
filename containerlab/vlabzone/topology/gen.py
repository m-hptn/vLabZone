#!/usr/bin/env python3
"""Generate the containerlab topologies from config/setups.yaml.

    topology/gen.py            write topology/vlabzone-{vrf,pbr}.clab.yml and their viewer layouts
                               (vlabzone-*.clab.yml.annotations.json, containerlab VS Code extension)
    topology/gen.py --check    exit 1 if a committed topology differs from what would be generated

The layout is always written in full: every node gets a position, an icon and a group
(corporate, lab core, one group per system switch). Positions moved in the viewer are
overwritten by the next `make topology` / `make up`. Only the viewer settings (style
preferences) of an existing layout file are kept. The layout files are not committed.

Both labs share everything except the edge mode and, in PBR mode, the setup
gateways gw-N. Generating them from one source keeps them from drifting apart,
and a new setup in setups.yaml shows up in both labs.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
MODES = ("vrf", "pbr")

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
}


def node(image: str, **env) -> dict:
    n = {"image": f"hwpool/{image}:latest"}
    if env:
        n["env"] = {k: str(v) for k, v in env.items()}
    return n


def build(mode: str, cfg: dict) -> dict:
    agg = cfg["aggregation"]
    nodes = {
        # corporate (simulated)
        "corp-fw": node("router", ROLE="corp-fw"),
        "corp-svc": node("device", NAME="corp-svc", SWITCH="corp", IP="10.20.20.10/24", GW="10.20.20.1"),
        "ws-std": node("workspace", WS_IP="10.20.10.11/24", WS_MODE="desktop"),
        "ws-power": node("workspace", WS_IP="10.20.10.12/24", WS_MODE="power"),
        "ws-ci": node("workspace", WS_IP="10.20.10.13/24", WS_MODE="alias"),
        # lab zone
        "lab-edge": node("edge", EDGE_MODE=mode),
        "broker": node("broker", EDGE_IMPL="${EDGE_IMPL:=linux}", EDGE_MODE=mode),
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
    if mode == "pbr":
        for s in cfg["setups"]:
            gw = f"gw-{s['id']}"
            nodes[gw] = node("router", ROLE="gw", SETUP=s["id"])
            links.append((f"{gw}:eth1", f"lab-{agg['name']}:{s['pbr_gateway_port']}"))
    mgmt_ports = [f"eth{i}" for i in range(1, len(mgmt_members) + 1)]
    nodes["lab-mgmt"] = node("mgmt", PORTS=" ".join(mgmt_ports))
    links += [(member, f"lab-mgmt:{port}") for member, port in zip(mgmt_members, mgmt_ports)]

    return {
        "name": f"vlabzone-{mode}",
        "mgmt": {
            # always created by containerlab, but no node is attached (network-mode: none)
            "network": "vlab-mgmt",
            "ipv4-subnet": "${VLAB_MGMT_SUBNET:=172.31.254.0/24}",
            "ipv6-subnet": "${VLAB_MGMT_SUBNET6:=3fff:172:31:254::/64}",
        },
        "topology": {
            "defaults": {
                "kind": "linux",
                # no Docker management interface: nothing can bypass corp-fw
                "network-mode": "none",
                "binds": ["../config:/etc/vlab:ro"],
            },
            "nodes": nodes,
            "links": [{"endpoints": list(pair)} for pair in links],
        },
    }


# --- viewer layout (containerlab VS Code extension, tested with 0.27) --------------------
# Positions are the top-left corner of a node; the viewer sizes each group around its members.
COL = {"corp": 0, "fw": 220, "edge": 460, "agg": 700, "sys": 1080, "dev": 1300}
ROW = 140                       # vertical distance between nodes in a column
SYS_GAP = 100                   # extra space between two system switch blocks

GROUP_STYLE = {
    "corporate": ("Corporate (simulated)", "#3b82f6"),
    "lab-core": ("Lab zone: edge, aggregation, management", "#f59e0b"),
}
SYS_COLOR = "#10b981"


def layout(mode: str, cfg: dict, viewer_settings: dict | None) -> dict:
    nodes: list[dict] = []

    def place(node_id: str, x: int, y: int, icon: str, group: str) -> None:
        nodes.append({"id": node_id, "position": {"x": x, "y": y}, "icon": icon, "groupId": group})

    # system switch blocks on the right decide the overall height
    groups = []
    y = 0
    sys_rows = []
    for sw in cfg["switches"]:
        n = len(sw["devices"])
        top = y
        for i, d in enumerate(sw["devices"]):
            place(d["name"], COL["dev"], top + i * ROW, "client", f"sys-{sw['name']}")
        place(f"lab-{sw['name']}", COL["sys"], top + (n - 1) * ROW // 2, "switch", f"sys-{sw['name']}")
        sys_rows.append(top + (n - 1) * ROW // 2)
        setups = [f"setup {s['id']}: " + ", ".join(d.split("-")[-1] for d in s["devices"])
                  for s in cfg["setups"] if s["switch"] == sw["name"]]
        groups.append((f"sys-{sw['name']}", f"System switch {sw['name']} – " + (" · ".join(setups) or "no setup"), SYS_COLOR))
        y = top + n * ROW + SYS_GAP
    mid = (sys_rows[0] + sys_rows[-1]) // 2

    # corporate side on the left
    for i, ws in enumerate(["ws-std", "ws-power", "ws-ci"]):
        place(ws, COL["corp"], mid - ROW * 3 // 2 + i * ROW, "vm", "corporate")
    place("corp-svc", COL["corp"], mid + ROW * 3 // 2 + 20, "server", "corporate")
    place("corp-fw", COL["fw"], mid, "firewall", "corporate")

    # lab core in the middle: edge, broker, aggregation, management, (PBR) gateways
    agg = cfg["aggregation"]["name"]
    place("lab-edge", COL["edge"], mid, "router", "lab-core")
    place("broker", COL["edge"], mid + ROW * 2, "controller", "lab-core")
    place(f"lab-{agg}", COL["agg"], mid, "switch", "lab-core")
    place("lab-mgmt", COL["agg"], mid + ROW * 2, "switch", "lab-core")
    if mode == "pbr":
        for i, s in enumerate(cfg["setups"]):
            place(f"gw-{s['id']}", COL["agg"] - 120 + i * 120, mid - ROW * 2 + 20, "router", "lab-core")
    groups = [(gid, *GROUP_STYLE[gid]) for gid in ("corporate", "lab-core")] + groups

    return {
        "freeTextAnnotations": [],
        "freeShapeAnnotations": [],
        "trafficRateAnnotations": [],
        "scriptButtonAnnotations": [],
        "groupStyleAnnotations": [
            {"id": gid, "name": name, "level": "1", "backgroundColor": color, "backgroundOpacity": 10,
             "borderColor": color, "borderWidth": 2, "borderStyle": "dashed", "borderRadius": 8,
             "labelColor": color, "labelPosition": "top-left"}
            for gid, name, color in groups
        ],
        "networkNodeAnnotations": [],
        "nodeAnnotations": nodes,
        "edgeAnnotations": [],
        "aliasEndpointAnnotations": [],
        "viewerSettings": viewer_settings or {"nodeStyle": "boxed"},
    }


def write_layout(mode: str, cfg: dict) -> None:
    path = ROOT / "topology" / f"vlabzone-{mode}.clab.yml.annotations.json"
    settings = None
    if path.exists():
        try:
            settings = json.loads(path.read_text()).get("viewerSettings")
        except (ValueError, AttributeError):
            settings = None
    text = json.dumps(layout(mode, cfg, settings), indent=2) + "\n"
    if not path.exists() or path.read_text() != text:
        path.write_text(text)
        print(f"wrote {path.relative_to(ROOT)}")


def render(mode: str, cfg: dict) -> str:
    header = (
        f"# GENERATED by topology/gen.py from config/setups.yaml – do not edit, run `make topology`.\n"
        f"#\n# vLabZone lab, edge mode {mode.upper()} (idea.md §4, addresses §4.3, nodes: docs/lab-{mode}.md)\n#\n"
        f"{DIAGRAM[mode]}\n"
    )
    body = yaml.safe_dump(build(mode, cfg), sort_keys=False, width=200, default_flow_style=False)
    return header + body


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="only check that the files are up to date")
    args = ap.parse_args()

    cfg = yaml.safe_load((ROOT / "config" / "setups.yaml").read_text())
    stale = []
    for mode in MODES:
        path = ROOT / "topology" / f"vlabzone-{mode}.clab.yml"
        text = render(mode, cfg)
        if args.check:
            if not path.exists() or path.read_text() != text:
                stale.append(path.name)
        else:
            if not path.exists() or path.read_text() != text:
                path.write_text(text)
                print(f"wrote {path.relative_to(ROOT)}")
            write_layout(mode, cfg)
    if stale:
        print(f"out of date: {', '.join(stale)} – run `make topology`", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
