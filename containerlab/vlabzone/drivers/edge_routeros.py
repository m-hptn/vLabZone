#!/usr/bin/env python3
"""RouterOS lab edge (MikroTik) – configuration and access control over the REST API.

The RouterOS counterpart of the Linux edge (images/edge/*.sh + edge-ctl). One driver for
all three edge modes; the design per mode is the one tried in stage 1b
(docs/routeros-spike.md) and documented in docs/lab-routeros.md.

    drivers/edge_routeros.py --lab vlabzone-hybrid-ros --mode hybrid configure
    drivers/edge_routeros.py --lab … --mode … peer add --setup 12 --pubkey KEY --ip 10.250.12.1
    drivers/edge_routeros.py --lab … --mode … peer del|list|pubkey|port|nat|neigh|status …

The commands after `configure` are the same as `edge-ctl` on the Linux edge, with the same
arguments, output and exit codes, so tests (and later the broker) treat both edges alike.

Interfaces (containerlab ethN = RouterOS ether(N+1)):
    ether1  containerlab management (vrnetlab; REST from the lab host)   [sim]
    ether2  corporate side 10.20.99.2/30 (clab eth1, to corp-fw)
    ether3  trunk to the aggregation switch (clab eth2)
    ether4  lab management 10.198.0.2/24 (clab eth3, broker)
"""
from __future__ import annotations

import argparse
import importlib.machinery
import importlib.util
import ipaddress
import re
import subprocess
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
AUTH = ("admin", "admin")              # vrnetlab default – lab only
TAG = "vlab:"                          # every object the driver owns carries this comment prefix
CORP, TRUNK, MGMT = "ether2", "ether3", "ether4"


def _vlab_cfg():
    path = ROOT / "images" / "base" / "rootfs" / "usr" / "local" / "bin" / "vlab-cfg"
    loader = importlib.machinery.SourceFileLoader("vlab_cfg", str(path))
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader("vlab_cfg", loader))
    loader.exec_module(mod)
    return mod


VC = _vlab_cfg()


class EdgeError(Exception):
    pass


class REST:
    def __init__(self, host: str) -> None:
        self.base = f"http://{host}/rest"

    def _check(self, r, what):
        if not r.ok:
            raise EdgeError(f"{what}: {r.status_code} {r.text}")
        return r.json() if r.text else None

    def get(self, path: str, **params):
        return self._check(requests.get(f"{self.base}/{path}", auth=AUTH, params=params, timeout=15), f"GET {path}")

    def put(self, path: str, **data):
        return self._check(requests.put(f"{self.base}/{path}", auth=AUTH, json=data, timeout=15), f"PUT {path} {data}")

    def patch(self, path: str, item_id: str, **data):
        return self._check(requests.patch(f"{self.base}/{path}/{item_id}", auth=AUTH, json=data, timeout=15),
                           f"PATCH {path}")

    def delete(self, path: str, item_id: str) -> None:
        r = requests.delete(f"{self.base}/{path}/{item_id}", auth=AUTH, timeout=15)
        if r.status_code != 404:
            self._check(r, f"DELETE {path}/{item_id}")

    def ensure(self, path: str, match: dict, **extra):
        """Create unless an item with all <match> fields exists (idempotent)."""
        for item in self.get(path):
            if all(item.get(k) == str(v) for k, v in match.items()):
                return item
        return self.put(path, **match, **extra)


def mgmt_ip(lab: str) -> str:
    out = subprocess.run(["docker", "inspect", "-f", "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}",
                          f"clab-{lab}-lab-edge"], capture_output=True, text=True)
    if out.returncode or not out.stdout.strip():
        raise EdgeError(f"lab-edge of lab {lab} not running")
    return out.stdout.strip()


class Edge:
    def __init__(self, lab: str, mode: str, cfg: dict | None = None) -> None:
        if mode not in ("vrf", "pbr", "hybrid"):
            raise EdgeError(f"unknown edge mode {mode}")
        self.lab, self.mode = lab, mode
        self.cfg = cfg or VC.load(str(ROOT / "config" / "setups.yaml"))
        self.ros = REST(mgmt_ip(lab))
        e = self.cfg["edge"]
        self.tun_ip, self.tun_net = e["tunnel_ip"], e["tunnel_net"]
        self.tun_prefix = self.tun_net.split("/")[1]
        self.port_base, self.port_one = int(e["wg_port_base"]), int(e["wg_port_pbr"])

    # --- helpers -------------------------------------------------------------------------
    def setups(self) -> list[dict]:
        return self.cfg["setups"]

    def setup(self, sid) -> dict:
        for s in self.setups():
            if str(s["id"]) == str(sid):
                return s
        raise EdgeError(f"unknown setup {sid}")

    def wg_if(self, sid) -> str:
        return f"wg-s{sid}" if self.mode == "vrf" else "wg0"

    def identity(self) -> str:
        return f"lab-edge-{self.mode}"

    def wait_ready(self, timeout: int = 300) -> float:
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout:
            try:
                self.ros.get("system/identity")
                return time.monotonic() - t0
            except Exception:
                time.sleep(2)
        raise EdgeError(f"RouterOS REST API not reachable after {timeout}s")

    def configured(self) -> bool:
        try:
            return self.ros.get("system/identity")["name"] == self.identity()
        except Exception:
            return False

    def _wait_interface(self, name: str) -> None:
        # like the tables, a new VRF's interface is known to the firewall only after a moment
        t0 = time.monotonic()
        while name not in [i["name"] for i in self.ros.get("interface")]:
            if time.monotonic() - t0 > 20:
                raise EdgeError(f"interface {name} did not appear")
            time.sleep(0.5)

    def _wait_table(self, name: str) -> None:
        # a VRF's routing table appears asynchronously; REST rejects it as unknown until then
        t0 = time.monotonic()
        while name not in [t["name"] for t in self.ros.get("routing/table")]:
            if time.monotonic() - t0 > 20:
                raise EdgeError(f"routing table {name} did not appear")
            time.sleep(0.5)

    # --- configuration -------------------------------------------------------------------
    def configure(self) -> None:
        r = self.ros
        r.ensure("ip/address", {"address": "10.20.99.2/30", "interface": CORP})
        r.ensure("ip/address", {"address": "10.198.0.2/24", "interface": MGMT})
        r.ensure("ip/route", {"dst-address": "10.20.0.0/16", "gateway": "10.20.99.1"},
                 comment=f"{TAG} corporate networks only, no default route")
        getattr(self, f"_configure_{self.mode}")()
        self._alias()
        self._firewall()
        # the identity marks the router as configured (Edge.configured, test fixtures)
        requests.post(f"{r.base}/system/identity/set", auth=AUTH, json={"name": self.identity()}, timeout=15)

    def _vlan(self, s: dict) -> str:
        name = f"vlan{s['vlan']}"
        self.ros.ensure("interface/vlan", {"name": name}, **{"vlan-id": str(s["vlan"]), "interface": TRUNK})
        return name

    def _setup_net(self, s: dict, vlan: str) -> None:
        prefix = s["net"].split("/")[1]
        self.ros.ensure("ip/address", {"address": f"{s['gateway']}/{prefix}", "interface": vlan})
        self.ros.ensure("ip/firewall/nat", {"chain": "srcnat", "out-interface": vlan},
                        action="masquerade", comment=f"{TAG} nat setup {s['id']}")

    def _configure_vrf(self) -> None:
        for s in self.setups():
            vlan, wg = self._vlan(s), f"wg-s{s['id']}"
            self.ros.ensure("interface/wireguard", {"name": wg}, **{"listen-port": str(self.port_base + s["id"])})
            self.ros.ensure("ip/vrf", {"name": f"vrf-s{s['id']}"}, interfaces=f"{wg},{vlan}")
            self.ros.ensure("ip/address", {"address": f"{self.tun_ip}/{self.tun_prefix}", "interface": wg})
            self._setup_net(s, vlan)
        for s in self.setups():
            self._wait_interface(f"vrf-s{s['id']}")

    def _configure_pbr(self) -> None:
        t = self.cfg["edge"]["transfer"]
        vlan = f"vlan{t['vlan']}"
        self.ros.ensure("interface/vlan", {"name": vlan}, **{"vlan-id": str(t["vlan"]), "interface": TRUNK})
        self.ros.ensure("ip/address", {"address": f"{t['edge_ip']}/{t['net'].split('/')[1]}", "interface": vlan})
        self._wg0()
        for s in self.setups():
            table = f"s{s['id']}"
            self.ros.ensure("routing/table", {"name": table}, fib="true")
            self.ros.ensure("ip/route", {"dst-address": "0.0.0.0/0", "routing-table": table},
                            gateway=VC.gw_ip(self.cfg, s["id"]), comment=f"{TAG} setup {s['id']} -> gw-{s['id']}")

    def _configure_hybrid(self) -> None:
        self._wg0()
        for s in self.setups():
            sid, vlan, vrf = s["id"], self._vlan(s), f"vrf-s{s['id']}"
            self.ros.ensure("ip/vrf", {"name": vrf}, interfaces=vlan)
            self._setup_net(s, vlan)
            self._wait_table(vrf)
            self.ros.ensure("ip/route", {"dst-address": self.tun_net, "routing-table": vrf},
                            gateway="wg0@main", comment=f"{TAG} setup {sid} -> tunnels")
            self.ros.ensure("routing/rule", {"src-address": VC.setup_prefix(self.cfg, sid), "interface": "wg0"},
                            table=vrf, action="lookup-only-in-table", comment=f"{TAG} setup {sid}")

    def _wg0(self) -> None:
        self.ros.ensure("interface/wireguard", {"name": "wg0"}, **{"listen-port": str(self.port_one)})
        self.ros.ensure("ip/address", {"address": f"{self.tun_ip}/{self.tun_prefix}", "interface": "wg0"})

    def _alias(self) -> None:
        for s in self.setups():
            self.ros.ensure("ip/firewall/nat", {"chain": "dstnat", "src-address": VC.setup_prefix(self.cfg, s["id"]),
                                                "dst-address": VC.alias_prefix(self.cfg, s["id"])},
                            action="netmap", **{"to-addresses": s["net"]}, comment=f"{TAG} alias setup {s['id']}")

    def firewall_rules(self) -> tuple[list, list]:
        """(mangle, filter) – rebuilt from scratch by configure()."""
        mangle, f = [], []
        ports = (f"{self.port_base}-{self.port_base + 299}" if self.mode == "vrf" else str(self.port_one))
        f += [("input", {"connection-state": "established,related", "action": "accept"}),
              ("input", {"connection-state": "invalid", "action": "drop"}),
              ("input", {"in-interface": "ether1", "action": "accept", "comment": "containerlab management (REST) [sim]"}),
              ("input", {"in-interface": MGMT, "protocol": "tcp", "dst-port": "22,80", "action": "accept",
                         "comment": "broker: SSH/REST over lab management"}),
              ("input", {"in-interface": CORP, "protocol": "udp", "dst-port": ports, "action": "accept",
                         "comment": "WireGuard from corporate"})]
        if self.mode == "vrf":
            # in-interface=vrf-sN is also what traffic from the setup VLAN shows (members can't be named,
            # docs/routeros-spike.md §3): the source prefix keeps setup devices out
            f += [("input", {"in-interface": f"vrf-s{s['id']}", "src-address": VC.setup_prefix(self.cfg, s["id"]),
                             "dst-address": self.tun_ip, "protocol": "icmp",
                             "action": "accept", "comment": f"tunnel gateway ping s{s['id']}"}) for s in self.setups()]
        else:
            f.append(("input", {"in-interface": "wg0", "dst-address": self.tun_ip, "protocol": "icmp",
                                "action": "accept", "comment": "tunnel gateway ping"}))
        f += [("input", {"action": "drop", "comment": "input everything else"}),
              ("forward", {"connection-state": "established,related", "action": "accept"}),
              ("forward", {"connection-state": "invalid", "action": "drop"})]
        for s in self.setups():
            sid, prefix = s["id"], VC.setup_prefix(self.cfg, s["id"])
            if self.mode == "vrf":
                # inside a VRF the filter sees the VRF as in-interface (docs/routeros-spike.md §3)
                f.append(("forward", {"in-interface": f"vrf-s{sid}", "src-address": prefix,
                                      "dst-address": s["net"], "action": "accept", "comment": f"setup {sid}"}))
            elif self.mode == "hybrid":
                # out-interface=vlanN only matches in mangle (docs/routeros-spike.md §6)
                mangle.append(("forward", {"out-interface": f"vlan{s['vlan']}", "action": "mark-packet",
                                           "new-packet-mark": f"to-s{sid}", "passthrough": "yes",
                                           "comment": f"into setup {sid}"}))
                f.append(("forward", {"in-interface": "wg0", "packet-mark": f"to-s{sid}", "src-address": prefix,
                                      "action": "accept", "comment": f"setup {sid}"}))
        if self.mode == "pbr":
            vlan = f"vlan{self.cfg['edge']['transfer']['vlan']}"
            f.append(("forward", {"in-interface": "wg0", "out-interface": vlan, "action": "accept",
                                  "comment": "tunnels -> transfer VLAN (which gateway: routing rules)"}))
        f.append(("forward", {"action": "drop", "comment": "forward everything else"}))
        return mangle, f

    def _firewall(self) -> None:
        mangle, filt = self.firewall_rules()
        for path, rules in (("ip/firewall/filter", filt), ("ip/firewall/mangle", mangle)):
            for item in self.ros.get(path):
                self.ros.delete(path, item[".id"])
            for chain, rule in rules:
                rule = dict(rule)
                rule["comment"] = f"{TAG} {rule.get('comment', '')}".strip()
                self.ros.put(path, chain=chain, **rule)
        # RouterOS activates filter changes asynchronously (~3 s, stage 1b)
        time.sleep(3)

    # --- edge-ctl commands ---------------------------------------------------------------
    def _peer(self, sid, pubkey):
        for p in self.ros.get("interface/wireguard/peers"):
            if p.get("public-key") == pubkey and p.get("interface") == self.wg_if(sid):
                return p
        return None

    def peer_add(self, sid, pubkey: str, ip: str) -> str:
        s = self.setup(sid)
        if not re.fullmatch(r"[A-Za-z0-9+/]{42}[AEIMQUYcgkosw480]=", pubkey or ""):
            raise EdgeError("invalid public key")
        m = re.fullmatch(r"10\.250\.(\d+)\.(\d+)", ip or "")
        if not m or m.group(1) != str(s["id"]) or not 1 <= int(m.group(2)) <= 254:
            raise EdgeError(f"--ip must be 10.250.{s['id']}.<user 1-254> for setup {s['id']} (got '{ip}')")
        old = self._peer(sid, pubkey)
        if old:
            self.ros.delete("interface/wireguard/peers", old[".id"])
        self.ros.put("interface/wireguard/peers", interface=self.wg_if(sid), **{"public-key": pubkey,
                     "allowed-address": f"{ip}/32"}, comment=f"{TAG} setup {sid} {ip}")
        if self.mode == "pbr":
            self._del_rule(ip)
            self.ros.put("routing/rule", **{"src-address": f"{ip}/32"}, interface="wg0", table=f"s{sid}",
                         action="lookup-only-in-table", comment=f"{TAG} booking {ip}")
        return f"added peer {pubkey[:8]}… ip {ip} to setup {sid}"

    def _del_rule(self, ip: str) -> None:
        for rule in self.ros.get("routing/rule"):
            if rule.get("src-address") == f"{ip}/32":
                self.ros.delete("routing/rule", rule[".id"])

    def peer_del(self, sid, pubkey: str) -> str:
        self.setup(sid)
        p = self._peer(sid, pubkey)
        if p:
            ip = p["allowed-address"].split("/")[0]
            self.ros.delete("interface/wireguard/peers", p[".id"])
            if self.mode == "pbr":
                self._del_rule(ip)
            # end running sessions at once (E08), like conntrack -D on Linux
            for c in self.ros.get("ip/firewall/connection"):
                if c.get("src-address", "").split(":")[0] == ip:
                    self.ros.delete("ip/firewall/connection", c[".id"])
        return f"removed peer {pubkey[:8]}… from setup {sid}"

    def peer_list(self, sid=None) -> str:
        lines = []
        for p in self.ros.get("interface/wireguard/peers"):
            ip = p.get("allowed-address", "")
            s = ip.split(".")[2] if ip.startswith("10.250.") else "-"
            if sid is None or str(sid) == s:
                lines.append(f"{s} {p.get('public-key')} {ip} {p.get('last-handshake', '-')} {p.get('rx', 0)} {p.get('tx', 0)}")
        return "\n".join(lines)

    def _wg(self, sid) -> dict:
        self.setup(sid)
        name = self.wg_if(sid)
        for w in self.ros.get("interface/wireguard"):
            if w["name"] == name:
                return w
        raise EdgeError(f"{name} missing – run configure")

    def pubkey(self, sid) -> str:
        return self._wg(sid)["public-key"]

    def port(self, sid) -> str:
        return self._wg(sid)["listen-port"]

    def nat(self, sid, on: bool) -> str:
        if self.mode == "pbr":
            raise EdgeError(f"PBR mode: NAT runs on gw-{sid} (gw-ctl nat on|off there)")
        vlan = f"vlan{self.setup(sid)['vlan']}"
        for n in self.ros.get("ip/firewall/nat"):
            if n.get("chain") == "srcnat" and n.get("out-interface") == vlan:
                self.ros.patch("ip/firewall/nat", n[".id"], disabled="false" if on else "true")
        for c in self.ros.get("ip/firewall/connection"):          # established flows keep their NAT otherwise
            if c.get("src-address", "").startswith("10.250."):
                self.ros.delete("ip/firewall/connection", c[".id"])
        time.sleep(1)
        return f"NAT {'on' if on else 'off'} for setup {sid}"

    def neigh_flush(self, sid) -> str:
        """Forget the ARP entries of a setup's VLAN (after regrouping devices, E20)."""
        if self.mode == "pbr":
            raise EdgeError(f"PBR mode: the setup's neighbours live on gw-{sid}")
        vlan = f"vlan{self.setup(sid)['vlan']}"
        for a in self.ros.get("ip/arp"):
            if a.get("interface") == vlan and a.get("dynamic") == "true":
                self.ros.delete("ip/arp", a[".id"])
        return f"neighbours of setup {sid} flushed"

    def status(self) -> str:
        peers = self.ros.get("interface/wireguard/peers")
        out = [f"mode {self.mode} (RouterOS {self.ros.get('system/resource')['version']})"]
        for s in self.setups():
            n = sum(1 for p in peers if p.get("interface") == self.wg_if(s["id"])
                    and p.get("allowed-address", "").startswith(f"10.250.{s['id']}."))
            out.append(f"setup {s['id']} {self.wg_if(s['id'])} port {self.port(s['id'])} peers {n}")
        return "\n".join(out)


def edge_ctl(edge: Edge, argv: list[str]) -> str:
    """The edge-ctl command line (images/edge/edge-ctl) on RouterOS."""
    ap = argparse.ArgumentParser(prog="edge-ctl")
    ap.add_argument("cmd")
    ap.add_argument("sub", nargs="?")
    ap.add_argument("--setup")
    ap.add_argument("--pubkey")
    ap.add_argument("--ip")
    a = ap.parse_args(argv)
    if a.cmd == "peer" and a.sub == "add":
        return edge.peer_add(a.setup, a.pubkey, a.ip)
    if a.cmd == "peer" and a.sub == "del":
        return edge.peer_del(a.setup, a.pubkey)
    if a.cmd == "peer" and a.sub == "list":
        return edge.peer_list(a.setup)
    if a.cmd == "pubkey":
        return edge.pubkey(a.sub or a.setup)
    if a.cmd == "port":
        return edge.port(a.sub or a.setup)
    if a.cmd == "nat" and a.sub in ("on", "off"):
        return edge.nat(a.setup, a.sub == "on")
    if a.cmd == "neigh" and a.sub == "flush":
        return edge.neigh_flush(a.setup)
    if a.cmd == "status":
        return edge.status()
    raise EdgeError(f"unknown command {' '.join(argv)}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lab", required=True)
    ap.add_argument("--mode", required=True, choices=("vrf", "pbr", "hybrid"))
    ap.add_argument("args", nargs=argparse.REMAINDER)
    a = ap.parse_args()
    try:
        edge = Edge(a.lab, a.mode)
        if a.args[:1] == ["configure"]:
            waited = edge.wait_ready()
            edge.configure()
            print(f"lab-edge (RouterOS) configured for {a.mode} mode (REST up after {waited:.0f} s)")
            return 0
        if a.args[:1] == ["wait"]:
            edge.wait_ready()
            return 0 if edge.configured() else 1
        out = edge_ctl(edge, a.args)
        if out:
            print(out)
        return 0
    except EdgeError as e:
        print(f"edge-ctl: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
