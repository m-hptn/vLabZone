#!/usr/bin/env python3
"""Generate the containerlab topologies from config/setups.yaml.

    topology/gen.py            write topology/vlabzone-vrf.clab.yml and vlabzone-pbr.clab.yml
    topology/gen.py --check    exit 1 if a committed file differs from what would be generated

Both labs share everything except the edge mode and, in PBR mode, the setup
gateways gw-N. Generating them from one source keeps them from drifting apart,
and a new setup in setups.yaml shows up in both labs.
"""
from __future__ import annotations

import argparse
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
        elif not path.exists() or path.read_text() != text:
            path.write_text(text)
            print(f"wrote {path.relative_to(ROOT)}")
    if stale:
        print(f"out of date: {', '.join(stale)} – run `make topology`", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
