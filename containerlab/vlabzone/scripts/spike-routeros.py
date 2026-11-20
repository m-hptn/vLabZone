#!/usr/bin/env python3
"""Stage 1b – RouterOS trial (docs/routeros-spike.md).

Configures the CHR edge for two setups (12 and 7, both 192.168.1.0/24) through the RouterOS
REST API – the interface the broker would use against the real MikroTik – and checks the
risky assumptions one by one. Prints every step and writes results/spike-routeros-<mode>-<time>.json.

  scripts/spike-routeros.py [--mode vrf]   VRF design   (make spike-up,          lab vlab-ros)
  scripts/spike-routeros.py --mode pbr     PBR design   (make spike-up SPIKE=pbr, lab vlab-ros-pbr)
  scripts/spike-routeros.py --show         only print the CHR's relevant configuration
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tests.lib.node import Node  # noqa: E402

LAB = "vlab-ros"                   # set by --mode: vlab-ros (vrf) / vlab-ros-pbr (pbr)
AUTH = ("admin", "admin")          # vrnetlab default credentials
RESULTS: dict = {"checks": {}}


def step(msg: str) -> None:
    print(f"\n>>> {msg}", flush=True)


def check(name: str, ok: bool, detail="") -> bool:
    RESULTS["checks"][name] = {"ok": bool(ok), "detail": detail}
    print(f"  {'PASS' if ok else 'FAIL'} {name}" + (f" – {detail}" if detail != "" else ""), flush=True)
    return ok


class ROS:
    """Minimal RouterOS v7 REST client."""

    def __init__(self, host: str) -> None:
        self.base = f"http://{host}/rest"

    def get(self, path: str, **params):
        r = requests.get(f"{self.base}/{path}", auth=AUTH, params=params, timeout=10)
        r.raise_for_status()
        return r.json()

    def put(self, path: str, **data):
        r = requests.put(f"{self.base}/{path}", auth=AUTH, json=data, timeout=10)
        if not r.ok:
            raise RuntimeError(f"PUT {path} {data}: {r.status_code} {r.text}")
        return r.json()

    def delete(self, path: str, item_id: str) -> None:
        r = requests.delete(f"{self.base}/{path}/{item_id}", auth=AUTH, timeout=10)
        r.raise_for_status()

    def ensure(self, path: str, key: str, **data):
        """Create the item unless one with the same <key> exists (idempotent)."""
        for item in self.get(path):
            if item.get(key) == str(data[key]):
                return item
        return self.put(path, **data)


def chr_mgmt_ip() -> str:
    out = subprocess.run(["docker", "inspect", "-f", "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}",
                          f"clab-{LAB}-chr"], capture_output=True, text=True, check=True).stdout.strip()
    return out


def wait_for_rest(ros: ROS, timeout: int = 600) -> float:
    """Wait for the REST API; returns the seconds since the CHR container started."""
    from datetime import datetime, timezone
    started = subprocess.run(["docker", "inspect", "-f", "{{.State.StartedAt}}", f"clab-{LAB}-chr"],
                             capture_output=True, text=True).stdout.strip()
    start = datetime.fromisoformat(started[:26].rstrip("Z") + "+00:00")
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        try:
            ident = ros.get("system/identity")
            ver = ros.get("system/resource")["version"]
            RESULTS["routeros_version"] = ver
            since = (datetime.now(timezone.utc) - start).total_seconds()
            print(f"  REST up: identity {ident['name']}, RouterOS {ver} ({since:.0f}s after container start)")
            return since
        except Exception:
            time.sleep(1)
    raise SystemExit(f"CHR REST API not reachable after {timeout}s (container started {started})")


def configure(ros: ROS) -> None:
    """Edge in VRF mode for setups 12 and 7 – the same design as the Linux edge."""
    ros.ensure("ip/address", "address", address="10.20.10.1/24", interface="ether2")
    for sid, vlan in ((12, 112), (7, 107)):
        ros.ensure("interface/vlan", "name", name=f"vlan{vlan}", **{"vlan-id": str(vlan)}, interface="ether3")
        ros.ensure("interface/wireguard", "name", name=f"wg-s{sid}", **{"listen-port": str(51800 + sid)})
        ros.ensure("ip/vrf", "name", name=f"vrf-s{sid}", interfaces=f"wg-s{sid},vlan{vlan}")
        # same addresses in every VRF
        existing = [(a["address"], a["interface"]) for a in ros.get("ip/address")]
        for addr, iface in ((f"192.168.1.254/24", f"vlan{vlan}"), ("10.250.0.1/16", f"wg-s{sid}")):
            if (addr, iface) not in existing:
                ros.put("ip/address", address=addr, interface=iface)
        nat = [n for n in ros.get("ip/firewall/nat") if n.get("out-interface") == f"vlan{vlan}"]
        if not nat:
            ros.put("ip/firewall/nat", chain="srcnat", action="masquerade", **{"out-interface": f"vlan{vlan}"},
                    comment=f"setup {sid}")


# Findings (RouterOS 7.24.6): inside a VRF the firewall sees the VRF interface (vrf-sN) as
# in-interface. Rules that name a VRF member (wg-sN, vlanN – as in- OR out-interface) are flagged
# invalid; an interface list containing VRFs is shown valid but silently never matches.
# So: one rule per VRF, `in-interface=vrf-sN` plus addresses. (Linux counterpart: meta sdifname.)
SETUPS = ((12, 112), (7, 107))
FIREWALL = [
    # input: what may reach the edge ITSELF
    ("input", {"connection-state": "established,related", "action": "accept"}),
    ("input", {"connection-state": "invalid", "action": "drop"}),
    ("input", {"in-interface": "ether1", "action": "accept", "comment": "vlab: management (lab: REST/SSH)"}),
    ("input", {"in-interface": "ether2", "protocol": "udp", "dst-port": "51800-52099", "action": "accept",
               "comment": "vlab: WireGuard from corporate"}),
    *[("input", {"in-interface": f"vrf-s{sid}", "dst-address": "10.250.0.1", "protocol": "icmp",
                 "action": "accept", "comment": f"vlab: ping tunnel gateway s{sid}"}) for sid, _ in SETUPS],
    ("input", {"action": "drop", "comment": "vlab: input everything else"}),
    # forward: inside VRF N only tunnel -> setup, replies via connection tracking
    ("forward", {"connection-state": "established,related", "action": "accept"}),
    ("forward", {"connection-state": "invalid", "action": "drop"}),
    *[("forward", {"in-interface": f"vrf-s{sid}", "src-address": "10.250.0.0/16", "dst-address": "192.168.1.0/24",
                   "action": "accept", "comment": f"vlab: setup {sid}"}) for sid, _ in SETUPS],
    ("forward", {"action": "drop", "comment": "vlab: forward everything else"}),
]


def firewall(ros: ROS) -> None:
    """Edge firewall like the Linux edge's nft rules. Recreated from scratch (idempotent)."""
    for rule in ros.get("ip/firewall/filter"):
        ros.delete("ip/firewall/filter", rule[".id"])
    for chain, rule in FIREWALL:
        ros.put("ip/firewall/filter", chain=chain, **rule)


class Tunnel:
    def __init__(self, ros: ROS, ws: Node, setup: int, tun_ip: str, netns: str | None = None,
                 register: bool = True, src_ip: str | None = None, ifname: str | None = None,
                 pbr: bool = False, action: str = "lookup-only-in-table"):
        """VRF: peer on wg-s<setup>, port 51800+setup. PBR: peer on wg0 (51820) + routing rule
        src-address=<tun_ip> interface=wg0 -> table s<setup>: two changes per booking."""
        self.ros, self.ws, self.setup, self.netns = ros, ws, setup, netns
        self.ifname = ifname or f"wg-s{setup}"
        edge_if = "wg0" if pbr else f"wg-s{setup}"
        port = 51820 if pbr else 51800 + setup
        self.tun_ip, self.rule = tun_ip, None
        key = f"/run/vlab/{self.ifname}.key"
        ws.run(f"mkdir -p /run/vlab && umask 077 && wg genkey > {key}")
        self.pubkey = ws.run(f"wg pubkey < {key}").stdout.strip()
        self.peer = None
        if register:
            self.peer = ros.put("interface/wireguard/peers", interface=edge_if, **{"public-key": self.pubkey,
                                "allowed-address": f"{tun_ip}/32"}, comment=f"spike {tun_ip}")
            if pbr:
                self.rule = ros.put("routing/rule", **{"src-address": f"{tun_ip}/32"}, interface="wg0",
                                    table=f"s{setup}", action=action, comment=f"spike {tun_ip}")
        edge_pub = [w for w in ros.get("interface/wireguard") if w["name"] == edge_if][0]["public-key"]
        ws.run(f"ip link add {self.ifname} type wireguard && wg set {self.ifname} private-key {key} "
               f"peer {edge_pub} endpoint 10.20.10.1:{port} allowed-ips 0.0.0.0/0 persistent-keepalive 25")
        if netns:
            ws.run(f"ip netns add {netns} && ip link set {self.ifname} netns {netns} && ip -n {netns} link set lo up")
        addr = src_ip or tun_ip
        self.run(f"ip addr add {addr}/32 dev {self.ifname} && ip link set {self.ifname} up && "
                 f"ip route add 192.168.1.0/24 dev {self.ifname} src {addr} && "
                 f"ip route add 10.250.0.1/32 dev {self.ifname} src {addr}")

    def run(self, cmd: str, **kw):
        return self.ws.run(cmd, netns=self.netns, check=kw.pop("check", True), **kw)

    def ident(self, ip: str = "192.168.1.20", timeout: int = 4):
        r = self.run(f"curl -s -m {timeout} http://{ip}", check=False)
        return json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip() else None

    def close(self) -> None:
        if self.netns:
            self.ws.run(f"ip netns del {self.netns}", check=False)
        else:
            self.ws.run(f"ip link del {self.ifname}", check=False)
        if self.peer:
            self.ros.delete("interface/wireguard/peers", self.peer[".id"])
        if self.rule:
            self.ros.delete("routing/rule", self.rule[".id"])


# --- PBR design ----------------------------------------------------------------------------------------
PBR_FIREWALL = [
    ("input", {"connection-state": "established,related", "action": "accept"}),
    ("input", {"connection-state": "invalid", "action": "drop"}),
    ("input", {"in-interface": "ether1", "action": "accept", "comment": "vlab: management (lab: REST/SSH)"}),
    ("input", {"in-interface": "ether2", "protocol": "udp", "dst-port": "51820", "action": "accept",
               "comment": "vlab: WireGuard from corporate"}),
    ("input", {"in-interface": "wg0", "dst-address": "10.250.0.1", "protocol": "icmp", "action": "accept",
               "comment": "vlab: ping tunnel gateway"}),
    ("input", {"action": "drop", "comment": "vlab: input everything else"}),
    ("forward", {"connection-state": "established,related", "action": "accept"}),
    ("forward", {"connection-state": "invalid", "action": "drop"}),
    ("forward", {"in-interface": "wg0", "out-interface": "vlan199", "action": "accept",
                 "comment": "vlab: tunnels -> transfer VLAN (which gateway: routing rules)"}),
    ("forward", {"action": "drop", "comment": "vlab: forward everything else"}),
]


def configure_pbr(ros: ROS) -> None:
    """Edge in PBR mode: one wg0, transfer VLAN 199, a routing table per setup – like edge-pbr.sh."""
    ros.ensure("ip/address", "address", address="10.20.10.1/24", interface="ether2")
    ros.ensure("interface/vlan", "name", name="vlan199", **{"vlan-id": "199"}, interface="ether3")
    ros.ensure("ip/address", "address", address="10.199.0.1/24", interface="vlan199")
    ros.ensure("interface/wireguard", "name", name="wg0", **{"listen-port": "51820"})
    ros.ensure("ip/address", "address", address="10.250.0.1/16", interface="wg0")
    for sid in (12, 7):
        ros.ensure("routing/table", "name", name=f"s{sid}", fib="true")
        if not [r for r in ros.get("ip/route") if r.get("routing-table") == f"s{sid}"]:
            ros.put("ip/route", **{"dst-address": "0.0.0.0/0", "gateway": f"10.199.0.{sid}",
                                   "routing-table": f"s{sid}"}, comment=f"setup {sid} -> gw-{sid}")


def pbr_firewall(ros: ROS, rules=PBR_FIREWALL) -> None:
    for rule in ros.get("ip/firewall/filter"):
        ros.delete("ip/firewall/filter", rule[".id"])
    for chain, rule in rules:
        ros.put("ip/firewall/filter", chain=chain, **rule)


def wait_until(cond, timeout: float = 30) -> float:
    t0 = time.monotonic()
    while not cond() and time.monotonic() - t0 < timeout:
        time.sleep(0.5)
    return round(time.monotonic() - t0, 1)


def pbr_trial(ros: ROS) -> None:
    step("Configure PBR mode via REST: wg0 (51820), VLAN 199, routing tables s12 / s7")
    configure_pbr(ros)
    tables = {t["name"]: t.get("fib") for t in ros.get("routing/table")}
    routes = {r["routing-table"]: (r["gateway"], r.get("active")) for r in ros.get("ip/route")
              if r.get("routing-table") in ("s12", "s7")}
    check("routing tables s12 / s7 with default route via gw-N, active", routes.get("s12", ("", ""))[1] == "true"
          and routes.get("s7", ("", ""))[1] == "true", {"tables": tables, "routes": routes})

    ws, dev12 = Node(LAB, "ws"), Node(LAB, "dev12")
    probe = lambda node, ip, port, ns=None: node.run(f"timeout 2 bash -c '</dev/tcp/{ip}/{port}'",
                                                      netns=ns, check=False).returncode == 0
    tunnels: list[Tunnel] = []

    def rule_of(t: Tunnel) -> dict:
        return [r for r in ros.get("routing/rule") if r[".id"] == t.rule[".id"]][0]

    def set_rule(t: Tunnel, **fields) -> None:
        """Replace the tunnel's routing rule (REST: delete + add, keeps the test simple)."""
        old = rule_of(t)
        ros.delete("routing/rule", old[".id"])
        data = {"src-address": old["src-address"], "interface": "wg0", "table": old["table"],
                "action": old["action"], "comment": old.get("comment", "")}
        data.update(fields)
        t.rule = ros.put("routing/rule", **data)

    try:
        step("Booking for setup 12: WireGuard peer on wg0 + routing rule")
        before = (len(ros.get("interface/wireguard/peers")), len(ros.get("routing/rule")))
        t12 = Tunnel(ros, ws, 12, "10.250.1.12", pbr=True)
        tunnels.append(t12)
        after = (len(ros.get("interface/wireguard/peers")), len(ros.get("routing/rule")))
        check("two changes per booking (peer + routing rule)", (after[0] - before[0], after[1] - before[1]) == (1, 1),
              {"peers": after[0] - before[0], "rules": after[1] - before[1]})
        wait_until(lambda: t12.ident(timeout=1) is not None, 15)
        a = t12.ident()
        check("setup 12 reachable via gw-12, answer from dev12", a and a["device"] == "dev12", a)
        check("MASQUERADE on gw-12: device sees 192.168.1.254", a and a["peer"] == "192.168.1.254", a)

        step("Booking for setup 7 (same IP), in a namespace")
        t7 = Tunnel(ros, ws, 7, "10.250.1.7", netns="setup7", pbr=True)
        tunnels.append(t7)
        wait_until(lambda: t7.ident(timeout=1) is not None, 15)
        b = t7.ident()
        check("setup 7 reachable via gw-7, answer from dev7 (one port for both setups)", b and b["device"] == "dev7", b)
        check("setup 12 still answers from dev12", (t12.ident() or {}).get("device") == "dev12")

        step("Spoofing: setup 12 with another context's source address")
        spoof = Tunnel(ros, ws, 12, "10.250.3.12", netns="spoof", src_ip="10.250.1.12", ifname="wg-spoof", pbr=True)
        tunnels.append(spoof)
        check("spoofed source dropped (cryptokey routing)", spoof.ident(timeout=3) is None)

        step("Misconfiguration: routing rule of the booking missing")
        rid = t12.rule[".id"]
        saved = rule_of(t12)
        ros.delete("routing/rule", rid)
        time.sleep(2)
        missing = t12.ident(timeout=3)
        t12.rule = ros.put("routing/rule", **{"src-address": saved["src-address"], "interface": "wg0",
                                              "table": saved["table"], "action": saved["action"]})
        wait_until(lambda: t12.ident(timeout=1) is not None, 15)
        check("missing rule -> no access (main table has no route into a setup)", missing is None, missing)

        step("Misconfiguration: routing rule points to the wrong table")
        set_rule(t12, table="s7")
        time.sleep(2)
        wrong = t12.ident(timeout=3)
        set_rule(t12, table="s12")
        wait_until(lambda: (t12.ident(timeout=1) or {}).get("device") == "dev12", 15)
        check("wrong table -> Alice (booked 12) reaches setup 7 (expected PBR weakness)",
              wrong and wrong["device"] == "dev7", wrong)

        step("Edge addresses and corporate side from a tunnel, no firewall yet")
        t7.run("ip route replace default dev wg-s7 src 10.250.1.7")      # the user routes everything into the tunnel
        edge_from_tunnel = {f"{ip}:{p}": probe(ws, ip, p, "setup7")
                            for ip in ("10.250.0.1", "10.199.0.1", "10.20.10.1") for p in (22, 80)}
        RESULTS["checks"]["edge services reachable from a tunnel without firewall"] = {
            "ok": True, "detail": [k for k, v in edge_from_tunnel.items() if v]}
        print(f"  INFO reachable from a tunnel without filter: {[k for k, v in edge_from_tunnel.items() if v]}")
        open_corp = [p for p in (21, 22, 23, 80, 8291, 8728) if probe(ws, "10.20.10.1", p)]
        print(f"  INFO open on the corporate address without filter: {open_corp}")

        step("Rule action: lookup-only-in-table vs. lookup when the setup table has no route")
        corp = lambda: ws.run("ping -c1 -W2 10.20.10.11", netns="setup7", check=False).returncode == 0
        normal = corp()
        route7 = [r for r in ros.get("ip/route") if r.get("routing-table") == "s7"][0]
        ros.delete("ip/route", route7[".id"])          # e.g. gateway route removed / gw down
        time.sleep(2)
        only_table = corp()
        set_rule(t7, action="lookup")
        time.sleep(2)
        fallback = corp()
        fallback_edge = probe(ws, "10.20.10.1", 22, "setup7")
        set_rule(t7, action="lookup-only-in-table")
        ros.put("ip/route", **{"dst-address": "0.0.0.0/0", "gateway": "10.199.0.7", "routing-table": "s7"},
                comment="setup 7 -> gw-7")
        wait_until(lambda: t7.ident(timeout=1) is not None, 15)
        check("corporate net not reachable from a tunnel while the setup route exists", not normal)
        check("lookup-only-in-table + setup route missing -> fail-closed", not only_table)
        check("action=lookup + setup route missing -> falls back to main: tunnel reaches the corporate net",
              fallback, {"workspace 10.20.10.11": fallback, "edge 10.20.10.1:22": fallback_edge})

        step("Edge firewall for PBR (wg0 is not in a VRF: in-interface=wg0)")
        pbr_firewall(ros)
        RESULTS["firewall_active_after_s"] = wait_until(
            lambda: not probe(ws, "10.20.10.1", 22) and t12.ident(timeout=1) is not None)
        print(f"  INFO firewall in effect after {RESULTS['firewall_active_after_s']} s")
        invalid = [r.get("comment") for r in ros.get("ip/firewall/filter") if r.get("invalid") == "true"]
        check("no edge firewall rule is invalid (in-interface=wg0 / out-interface=vlan199 work in main)", not invalid, invalid)
        a, b = t12.ident(), t7.ident()
        fwd = {r.get("comment"): int(r.get("packets", 0)) for r in ros.get("ip/firewall/filter") if r["chain"] == "forward"}
        check("setups 12 and 7 work through the forward rule", (a or {}).get("device") == "dev12"
              and (b or {}).get("device") == "dev7" and fwd.get("vlab: tunnels -> transfer VLAN (which gateway: routing rules)", 0) > 0,
              {"setup12": a and a["device"], "setup7": b and b["device"]})
        check("management services closed on the corporate address",
              not [p for p in (21, 22, 23, 80, 8291, 8728) if probe(ws, "10.20.10.1", p)])
        after_fw = {f"{ip}:{p}": probe(ws, ip, p, "setup7") for ip in ("10.250.0.1", "10.199.0.1", "10.20.10.1") for p in (22, 80)}
        check("edge services not reachable from a tunnel", not any(after_fw.values()), [k for k, v in after_fw.items() if v])
        check("tunnel gateway ping allowed (in-interface=wg0)",
              ws.run("ping -c2 -W2 10.250.0.1", netns="setup7", check=False).returncode == 0)
        check("tunnel cannot ping the edge's transfer address 10.199.0.1",
              ws.run("ping -c1 -W2 10.199.0.1", netns="setup7", check=False).returncode != 0)
        route7 = [r for r in ros.get("ip/route") if r.get("routing-table") == "s7"][0]
        ros.delete("ip/route", route7[".id"])
        set_rule(t7, action="lookup")
        time.sleep(3)
        fallback_fw = corp()
        set_rule(t7, action="lookup-only-in-table")
        ros.put("ip/route", **{"dst-address": "0.0.0.0/0", "gateway": "10.199.0.7", "routing-table": "s7"},
                comment="setup 7 -> gw-7")
        wait_until(lambda: t7.ident(timeout=1) is not None, 15)
        check("with firewall: the lookup fallback no longer reaches the corporate net", not fallback_fw)
        dev12.run("ip route replace default via 192.168.1.254")
        try:
            dev_out = {t: dev12.run(f"ping -c1 -W2 {t}", check=False).returncode == 0
                       for t in ("10.199.0.1", "10.199.0.7", "10.250.1.12", "10.20.10.11")}
        finally:
            dev12.run("ip route replace default via 192.168.1.1")
        check("setup 12 reaches neither edge, other gateway, tunnels nor corporate net", not any(dev_out.values()), dev_out)

        step("Negative control: without the forward accept rule, every setup is closed")
        fw_rule = [r for r in ros.get("ip/firewall/filter") if (r.get("comment") or "").startswith("vlab: tunnels")][0]
        ros.delete("ip/firewall/filter", fw_rule[".id"])
        time.sleep(3)
        closed = t12.ident(timeout=2) is None and t7.ident(timeout=2) is None
        pbr_firewall(ros)
        wait_until(lambda: t12.ident(timeout=1) is not None, 30)
        check("setups closed without the forward rule", closed)

        step("Revoke: delete peer and routing rule via REST")
        ros.delete("interface/wireguard/peers", t12.peer[".id"])
        ros.delete("routing/rule", t12.rule[".id"])
        t12.peer = t12.rule = None
        check("access ends after revoke (no answer within 2 s)", t12.ident(timeout=2) is None)
        check("setup 7 unaffected by the revoke", (t7.ident() or {}).get("device") == "dev7")
    finally:
        for t in reversed(tunnels):
            t.close()



def show(ros: ROS) -> None:
    for path in ("interface", "ip/vrf", "ip/address", "interface/wireguard", "interface/wireguard/peers",
                 "ip/firewall/nat", "ip/route", "routing/table", "routing/rule"):
        print(f"--- /{path}")
        for item in ros.get(path):
            print("   ", {k: v for k, v in item.items() if k in (
                "name", "type", "interfaces", "address", "interface", "listen-port", "allowed-address",
                "out-interface", "action", "dst-address", "gateway", "routing-table", "vrf", "comment", "running",
                "src-address", "table", "fib", "active")})


def main() -> int:
    global LAB
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=("vrf", "pbr"), default="vrf")
    ap.add_argument("--show", action="store_true")
    args = ap.parse_args()
    LAB = "vlab-ros" if args.mode == "vrf" else "vlab-ros-pbr"
    RESULTS["mode"] = args.mode

    host = chr_mgmt_ip()
    ros = ROS(host)
    step(f"CHR management address {host} – waiting for the REST API")
    boot = wait_for_rest(ros)
    RESULTS["rest_ready_after_s"] = round(boot, 1)
    if args.show:
        show(ros)
        return 0

    step("Interface mapping (containerlab ethN -> RouterOS)")
    ifaces = {i["name"]: i.get("running") for i in ros.get("interface") if i["type"] == "ether"}
    print("  ", ifaces)
    check("ether1 = management, ether2/ether3 = clab eth1/eth2", {"ether1", "ether2", "ether3"} <= set(ifaces),
          sorted(ifaces))

    # every run starts without filter rules, so the "no firewall" steps are meaningful on reruns too
    for rule in ros.get("ip/firewall/filter"):
        ros.delete("ip/firewall/filter", rule[".id"])
    time.sleep(3)      # RouterOS applies filter changes asynchronously (~3 s)

    if args.mode == "pbr":
        pbr_trial(ros)
        return finish(ros, "pbr")

    step("Configure VRF mode for setups 12 and 7 via REST")
    configure(ros)
    vrfs = {v["name"]: v["interfaces"] for v in ros.get("ip/vrf")}
    check("VRFs with WireGuard + VLAN interface", vrfs.get("vrf-s12") and vrfs.get("vrf-s7"), vrfs)
    addrs = [(a["address"], a["interface"]) for a in ros.get("ip/address")]
    check("same 192.168.1.254/24 in both VRFs accepted",
          ("192.168.1.254/24", "vlan112") in addrs and ("192.168.1.254/24", "vlan107") in addrs)

    ws, dev12 = Node(LAB, "ws"), Node(LAB, "dev12")
    tunnels: list[Tunnel] = []
    try:
        step("Tunnel ws -> setup 12 (WireGuard interface inside vrf-s12, endpoint in main)")
        t12 = Tunnel(ros, ws, 12, "10.250.1.12")
        tunnels.append(t12)
        a = None
        for _ in range(10):
            a = t12.ident()
            if a:
                break
            time.sleep(1)
        hs = ws.run("wg show wg-s12 latest-handshakes").stdout.split()
        check("WireGuard handshake with an interface inside a VRF", len(hs) == 2 and hs[1] != "0", hs)
        check("setup 12 reachable, answer from dev12", a and a["device"] == "dev12", a)
        check("MASQUERADE: device sees 192.168.1.254", a and a["peer"] == "192.168.1.254", a)

        step("Second tunnel to setup 7 (same IP 192.168.1.20) in a namespace")
        t7 = Tunnel(ros, ws, 7, "10.250.1.7", netns="setup7")
        tunnels.append(t7)
        b = None
        for _ in range(10):
            b = t7.ident()
            if b:
                break
            time.sleep(1)
        check("setup 7 reachable, answer from dev7 (overlapping network)", b and b["device"] == "dev7", b)
        a2 = t12.ident()
        check("setup 12 still answers from dev12", a2 and a2["device"] == "dev12", a2)

        step("Isolation: a setup cannot reach the corporate side or the other setup")
        dev12.run("ip route replace default via 192.168.1.254")
        try:
            corp = dev12.run("ping -c1 -W2 10.20.10.11", check=False).returncode == 0
            edge_main = dev12.run("ping -c1 -W2 10.20.10.1", check=False).returncode == 0
        finally:
            dev12.run("ip route replace default via 192.168.1.1")
        check("setup 12 -> workspace 10.20.10.11 blocked (VRF)", not corp)
        RESULTS["checks"]["setup 12 -> edge 10.20.10.1 (main VRF address)"] = {"ok": not edge_main,
                                                                               "detail": "reachable" if edge_main else "blocked"}
        print(f"  INFO setup 12 -> edge main address 10.20.10.1: {'reachable' if edge_main else 'blocked'}")

        step("Spoofing: tunnel to setup 12 using another context's address")
        spoof = Tunnel(ros, ws, 12, "10.250.3.12", netns="spoof", src_ip="10.250.1.12", ifname="wg-spoof")
        tunnels.append(spoof)
        check("spoofed source dropped (cryptokey routing)", spoof.ident(timeout=3) is None)

        step("Edge services: reachable from the corporate side without a firewall?")
        probe = lambda node, ip, port, ns=None: node.run(f"timeout 2 bash -c '</dev/tcp/{ip}/{port}'",
                                                          netns=ns, check=False).returncode == 0
        open_before = [p for p in (21, 22, 23, 80, 8291, 8728) if probe(ws, "10.20.10.1", p)]
        RESULTS["checks"]["management services open on the corporate address without firewall"] = {
            "ok": True, "detail": open_before}
        print(f"  INFO open on 10.20.10.1 without filter: {open_before}")
        dev12.run("ip route replace default via 192.168.1.254")
        try:
            in_setup = [p for p in (22, 80, 8291) if probe(dev12, "192.168.1.254", p)]
        finally:
            dev12.run("ip route replace default via 192.168.1.1")
        check("services not reachable from inside a setup VRF (they listen in main)", not in_setup, in_setup)

        step("Firewall: how do rules refer to interfaces inside a VRF?")
        tests = {}
        for name, match in (("in-interface=wg-s12 (member)", {"in-interface": "wg-s12"}),
                            ("out-interface=vlan112 (member)", {"in-interface": "vrf-s12", "out-interface": "vlan112"}),
                            ("in-interface=vrf-s12", {"in-interface": "vrf-s12"})):
            rule = ros.put("ip/firewall/filter", chain="forward", action="passthrough", comment=f"test: {name}", **match)
            tests[name] = rule[".id"]
        time.sleep(3)
        flags = {n: r.get("invalid") for r in ros.get("ip/firewall/filter") for n, i in tests.items() if r[".id"] == i}
        for i in tests.values():
            ros.delete("ip/firewall/filter", i)
        check("rules naming a VRF member interface are flagged invalid",
              flags["in-interface=wg-s12 (member)"] == "true" and flags["out-interface=vlan112 (member)"] == "true", flags)
        check("rule with in-interface=vrf-s12 is valid", flags["in-interface=vrf-s12"] == "false", flags)

        step("Edge firewall (in-interface = VRF + addresses)")
        firewall(ros)
        # RouterOS activates new rules asynchronously: wait until they are in effect
        t_fw = time.monotonic()
        while (probe(ws, "10.20.10.1", 22) or not t12.ident(timeout=1)) and time.monotonic() - t_fw < 30:
            time.sleep(0.5)
        RESULTS["firewall_active_after_s"] = round(time.monotonic() - t_fw, 1)
        print(f"  INFO firewall in effect after {RESULTS['firewall_active_after_s']} s")
        invalid = [r.get("comment") for r in ros.get("ip/firewall/filter") if r.get("invalid") == "true"]
        check("no edge firewall rule is invalid", not invalid, invalid)
        a, b = t12.ident(), t7.ident()
        fwd = {r.get("comment"): int(r.get("packets", 0)) for r in ros.get("ip/firewall/filter") if r["chain"] == "forward"}
        check("setup 12 works through its forward rule", a and a["device"] == "dev12" and fwd.get("vlab: setup 12", 0) > 0,
              {"answer": a and a["device"], "counters": fwd})
        check("setup 7 works through its forward rule", b and b["device"] == "dev7" and fwd.get("vlab: setup 7", 0) > 0,
              {"answer": b and b["device"]})
        open_after = [p for p in (21, 22, 23, 80, 8291, 8728) if probe(ws, "10.20.10.1", p)]
        check("management services closed on the corporate address", not open_after, open_after)
        check("tunnel gateway ping allowed (input, in-interface=vrf-s12)",
              ws.run("ping -c2 -W2 10.250.0.1", check=False).returncode == 0)
        check("tunnel cannot open SSH on the edge (10.250.0.1:22)", not probe(ws, "10.250.0.1", 22))
        dev12.run("ip route replace default via 192.168.1.254")
        try:
            dev_ping = dev12.run("ping -c1 -W2 192.168.1.254", check=False).returncode == 0
            dev_to_tunnel = dev12.run("ping -c1 -W2 10.250.1.12", check=False).returncode == 0
        finally:
            dev12.run("ip route replace default via 192.168.1.1")
        check("setup device cannot reach the edge itself (input drop)", not dev_ping)
        check("setup device cannot open connections into the tunnel (forward drop)", not dev_to_tunnel)

        step("Negative control: without setup 12's forward rule, setup 12 is closed")
        rule12 = [r for r in ros.get("ip/firewall/filter") if r.get("comment") == "vlab: setup 12"][0]
        ros.delete("ip/firewall/filter", rule12[".id"])
        time.sleep(3)
        closed = t12.ident(timeout=2) is None
        still7 = t7.ident()
        firewall(ros)
        t_fw = time.monotonic()
        while not t12.ident(timeout=1) and time.monotonic() - t_fw < 30:
            time.sleep(0.5)
        check("setup 12 closed without its rule, setup 7 unaffected", closed and still7 and still7["device"] == "dev7",
              {"setup12_closed": closed, "setup7": still7 and still7["device"]})

        step("Revoke: delete the peer via REST while the tunnel is up")
        t0 = time.monotonic()
        ros.delete("interface/wireguard/peers", t12.peer[".id"])
        t12.peer = None
        gone = t12.ident(timeout=2) is None
        check("access ends after peer removal (no answer within 2 s)", gone)
    finally:
        for t in reversed(tunnels):
            t.close()

    return finish(ros, "vrf")


def finish(ros: ROS, mode: str) -> int:
    step("RouterOS configuration after the trial")
    show(ros)
    out = ROOT / "results" / f"spike-routeros-{mode}-{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(RESULTS, indent=2))
    failed = [k for k, v in RESULTS["checks"].items() if not v["ok"]]
    print(f"\n{len(RESULTS['checks']) - len(failed)} passed, {len(failed)} failed – {out.relative_to(ROOT)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
