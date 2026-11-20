#!/usr/bin/env python3
"""Stage 1b – RouterOS trial. Run after `make spike-up` (topology/spike-routeros.clab.yml).

Configures the CHR edge for two setups (12 and 7, both 192.168.1.0/24) through the RouterOS
REST API – the interface the broker would use against the real MikroTik – and checks the
risky assumptions one by one. Prints every step and writes results/spike-routeros-<time>.json.

  scripts/spike-routeros.py            configure + check
  scripts/spike-routeros.py --show     only print the CHR's relevant configuration
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

LAB = "vlab-ros"
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
                 register: bool = True, src_ip: str | None = None, ifname: str | None = None):
        self.ros, self.ws, self.setup, self.netns = ros, ws, setup, netns
        self.ifname = ifname or f"wg-s{setup}"
        edge_if = f"wg-s{setup}"
        key = f"/run/vlab/{self.ifname}.key"
        ws.run(f"mkdir -p /run/vlab && umask 077 && wg genkey > {key}")
        self.pubkey = ws.run(f"wg pubkey < {key}").stdout.strip()
        self.peer = None
        if register:
            self.peer = ros.put("interface/wireguard/peers", interface=edge_if, **{"public-key": self.pubkey,
                                "allowed-address": f"{tun_ip}/32"}, comment=f"spike {tun_ip}")
        edge_pub = [w for w in ros.get("interface/wireguard") if w["name"] == edge_if][0]["public-key"]
        ws.run(f"ip link add {self.ifname} type wireguard && wg set {self.ifname} private-key {key} "
               f"peer {edge_pub} endpoint 10.20.10.1:{51800 + setup} allowed-ips 0.0.0.0/0 persistent-keepalive 25")
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


def show(ros: ROS) -> None:
    for path in ("interface", "ip/vrf", "ip/address", "interface/wireguard", "interface/wireguard/peers",
                 "ip/firewall/nat", "ip/route"):
        print(f"--- /{path}")
        for item in ros.get(path):
            print("   ", {k: v for k, v in item.items() if k in (
                "name", "type", "interfaces", "address", "interface", "listen-port", "allowed-address",
                "out-interface", "action", "dst-address", "gateway", "routing-table", "vrf", "comment", "running")})


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--show", action="store_true")
    args = ap.parse_args()

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

    step("RouterOS configuration after the trial")
    show(ros)
    out = ROOT / "results" / f"spike-routeros-{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(RESULTS, indent=2))
    failed = [k for k, v in RESULTS["checks"].items() if not v["ok"]]
    print(f"\n{len(RESULTS['checks']) - len(failed)} passed, {len(failed)} failed – {out.relative_to(ROOT)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
