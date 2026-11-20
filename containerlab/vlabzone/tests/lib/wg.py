"""Create WireGuard contexts from a workspace to a setup – the stage 1/2 stand-in
for lab-agent + broker. The key is generated INSIDE the workspace; only the
public key leaves it (registered on the edge with `edge-ctl peer add`)."""
from __future__ import annotations

from dataclasses import dataclass

from tests.lib import labcfg
from tests.lib.node import Node

EDGE_ENDPOINT = "10.20.99.2"


def tunnel_ip(user: int, setup: int) -> str:
    """Tunnel address 10.250.<setup>.<user> (docs/tunnel-addresses.md; idea.md §4.3 had user and
    setup the other way round). Computed by the same code the nodes use (vlab-cfg)."""
    return labcfg.tunnel_ip(setup, user)


@dataclass
class Tunnel:
    ws: Node
    edge: Node
    user: int
    setup: int
    ifname: str
    pubkey: str
    ip: str
    netns: str | None = None
    registered: bool = True

    def run(self, cmd: str, **kw):
        """Run a command in the tunnel's context (namespace if any)."""
        return self.ws.run(cmd, netns=self.netns, **kw)

    def curl(self, url: str, timeout: int = 5, **kw):
        return self.run(f"curl -s -m {timeout} {url}", check=False, **kw)

    def close(self) -> None:
        if self.netns:
            self.ws.run(f"ip netns del {self.netns}", check=False)
        else:
            self.ws.run(f"ip link del {self.ifname}", check=False)
        if self.registered:
            self.edge.run(f"edge-ctl peer del --setup {self.setup} --pubkey {self.pubkey}", check=False)


def connect(ws: Node, edge: Node, user: int, setup: int, *, netns: str | None = None,
            register: bool = True, ip: str | None = None, routes: tuple[str, ...] = ("192.168.1.0/24",)) -> Tunnel:
    """Bring up wg-s<setup> in `ws` (optionally inside network namespace `netns`).

    register=False creates the interface without registering the key on the edge
    (used for negative tests). `ip` overrides the tunnel address (spoofing tests).
    """
    ifname = f"wg-s{setup}"
    own_ip = tunnel_ip(user, setup)
    addr = ip or own_ip
    keyfile = f"/run/vlab/{ifname}.key"

    ws.run(f"mkdir -p /run/vlab && umask 077 && wg genkey > {keyfile}")
    pubkey = ws.run(f"wg pubkey < {keyfile}").stdout.strip()
    if register:
        edge.run(f"edge-ctl peer add --setup {setup} --pubkey {pubkey} --ip {own_ip}")
    edge_pub = edge.run(f"edge-ctl pubkey --setup {setup}").stdout.strip()
    port = edge.run(f"edge-ctl port --setup {setup}").stdout.strip()

    # Created in the init namespace: the encrypted UDP socket stays there (corporate side),
    # the interface itself can then be moved into a namespace (power-user mode).
    ws.run(f"ip link add {ifname} type wireguard && "
           f"wg set {ifname} private-key {keyfile} peer {edge_pub} endpoint {EDGE_ENDPOINT}:{port} "
           f"allowed-ips 0.0.0.0/0 persistent-keepalive 25")
    if netns:
        ws.run(f"ip netns add {netns} && ip link set {ifname} netns {netns} && ip -n {netns} link set lo up")
    t = Tunnel(ws, edge, user, setup, ifname, pubkey, addr, netns, register)
    t.run(f"ip addr add {addr}/32 dev {ifname} && ip link set {ifname} up && "
          f"ip route replace 10.250.0.1/32 dev {ifname} src {addr}")   # replace: several tunnels in one namespace (alias mode)
    for net in routes:
        t.run(f"ip route add {net} dev {ifname} src {addr}")
    return t
