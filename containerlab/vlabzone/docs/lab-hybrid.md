# Lab `vlabzone-hybrid` – one WireGuard port, setups in VRFs on the edge

Start: `make up EDGE_MODE=hybrid` · Containers: `clab-vlabzone-hybrid-<node>` · 19 nodes ·
Topology: [`vlabzone-hybrid.clab.yml`](../topology/vlabzone-hybrid.clab.yml) (generated)

The nodes that are the same in every lab (corporate firewall, workspaces, devices, broker, switches,
management) are described in [common.md](common.md). This document covers what makes the hybrid lab what it is.

**Why this mode exists:** the corporate firewall should need **one rule with one port**. The VRF design needs
one port per setup (51800 + N), and PBR needs a gateway per setup, a routing rule per booking and a transfer
VLAN shared by all setups. The hybrid keeps PBR's single port and VRF's setups on the edge itself:

| | VRF | PBR | **hybrid** |
|---|---|---|---|
| WireGuard on the edge | `wg-sN` per setup, port 51800 + N | one `wg0`, port 51820 | one `wg0`, port 51820 |
| Setup network on the edge | VLAN interface in `vrf-sN` | none (gateway gw-N) | VLAN interface in `vrf-sN` |
| What selects the setup | the port the workspace connects to | `ip rule` per **booking** → table → gw-N | `ip rule` per **setup** (tunnel prefix) → the VRF's table |
| Changes per booking | 1 (peer) | 2 (peer + rule) | **1 (peer)** |
| MASQUERADE | edge | gw-N | edge |

The per-setup rule only works because the tunnel addresses are **10.250.\<setup\>.\<user\>**: all contexts of
setup 12 lie in `10.250.12.0/24`. Why the address layout was changed for this: [tunnel-addresses.md](tunnel-addresses.md).

---

## 1. Overview

```text
                  corporate (simulated)                    │                            lab zone
                                                           │
 ws-std   10.20.10.11 ─┐                                   │                               ┌ eth11 sys1-dev1 .20 ┐ setup 12
 ws-power 10.20.10.12 ─┼─ br-ws ┐                          │                               ├ eth12 sys1-dev2 .21 ┘ VLAN 112
 ws-ci    10.20.10.13 ─┘        │                          │            ┌═ eth11 ═ lab-sys1 ┼ eth13 sys1-dev3 .20 ┐ setup 7
                             corp-fw ─ UDP 51820 ─ lab-edge ═ 112,107,105 ═ lab-agg         └ eth14 sys1-dev4 .22 ┘ VLAN 107
 corp-svc 10.20.20.10 ── eth4 ─┘  └─ TCP 443/8443 ─ broker              │
                                                           │            └═ eth12 ═ lab-sys2 ┬ eth11 sys2-dev1 .20 ┐ setup 5
                                                           │                                ├ eth12 sys2-dev2 .21 ┘ VLAN 105
                                                           │                                ├ eth13 sys2-dev3 .22   ungrouped
                                                           │                                └ eth14 sys2-dev4 .20   ungrouped
                                                           │  lab-mgmt 10.198.0.0/24: edge, broker, agg, sys1, sys2
```

Inside the edge:

```text
             ┌───────────────────────────────── lab-edge ──────────────────────────────────┐
 UDP 51820 ──┤ wg0 10.250.0.1 (default VRF), one peer per booking (10.250.<setup>.<user>/32) │
 (eth1)      │  ip rule from 10.250.12.0/24 iif wg0 lookup 1012 ─→ vrf-s12: eth2.112 .254 ─┼── VLAN 112
             │  ip rule from 10.250.7.0/24  iif wg0 lookup 1007 ─→ vrf-s7:  eth2.107 .254 ─┼── VLAN 107
             │  ip rule from 10.250.5.0/24  iif wg0 lookup 1005 ─→ vrf-s5:  eth2.105 .254 ─┼── VLAN 105
             │  ip rule iif wg0 unreachable (catch-all)                                    │
             │  each VRF table also: 10.250.0.0/16 dev wg0 (way back to the tunnels)       │
             └──────────────────────────────────────────────────────────────────────────────┘
```

The switches are configured as in the VRF lab: the edge trunk carries every setup VLAN (plus VLAN 197 for the serial/USB appliances, route leak into every setup VRF, [consoles-usb.md](consoles-usb.md)), no transfer VLAN,
no gateways (`vlab-cfg switch-config agg` with `EDGE_MODE=hybrid`).

### Addressing

As in the VRF lab ([lab-vrf.md](lab-vrf.md#addressing)), except:

| Segment | Network | Why this way |
|---|---|---|
| WireGuard tunnels | 10.250.0.0/16 on `wg0`, edge 10.250.0.1 | context = **10.250.\<setup\>.\<user\>**, setup N = `10.250.N.0/24`. The edge selects the setup by this prefix and checks it in the forward filter ([tunnel-addresses.md](tunnel-addresses.md)) |
| corp-fw allowance | UDP **51820** only | `corp-fw.sh` writes the port per mode: VRF 51800–52099, PBR and hybrid 51820 (E01 checks it) |

---

## 2. One request

Alice (user 1) in `ws-std` has a tunnel to setup 12, address `10.250.12.1`, and runs
`curl http://192.168.1.20`. Outputs from the running lab:

```text
$ ip rule show                                   # on lab-edge
0:      from all lookup local
1000:   from all lookup [l3mdev-table]
10000:  from 10.250.12.0/24 iif wg0 lookup 1012  # setup 12 – written at start, not per booking
10000:  from 10.250.7.0/24 iif wg0 lookup 1007
10000:  from 10.250.5.0/24 iif wg0 lookup 1005
30000:  from all iif wg0 unreachable             # catch-all
32766:  from all lookup main
32767:  from all lookup default

$ wg show wg0 allowed-ips                        # Alice's booking: the only per-booking object
6PdVM0hz…Vih4=  10.250.12.1/32

$ ip route show table 1012                       # vrf-s12
10.250.0.0/16 dev wg0 scope link                 # back to the tunnels (wg0 is outside the VRF)
192.168.1.0/24 dev eth2.112 proto kernel scope link src 192.168.1.254
local 192.168.1.254 dev eth2.112 proto kernel scope host src 192.168.1.254

$ ip route get 192.168.1.20 from 10.250.12.1 iif wg0
192.168.1.20 from 10.250.12.1 dev eth2.112 table 1012

$ ip route get 10.20.20.10 from 10.250.12.1 iif wg0     # corporate net from the tunnel
RTNETLINK answers: Network is unreachable
```

1. WireGuard decrypts on `wg0`; the key allows only source `10.250.12.1/32`.
2. The rule for `10.250.12.0/24` sends the lookup into table 1012, the table of `vrf-s12`. A route from the
   default VRF into a VRF's table works in Linux for forwarded traffic: the packet leaves on `eth2.112`.
3. Forward filter: `iifname "wg0" oifname "eth2.112" ip saddr 10.250.12.0/24 accept`.
4. MASQUERADE on `eth2.112`: the device sees `192.168.1.254`, as in the VRF lab.
5. The reply arrives on `eth2.112`, inside `vrf-s12`. Conntrack reverses the NAT, the lookup in table 1012 finds
   `10.250.0.0/16 dev wg0`, and WireGuard picks the peer whose allowed IPs contain `10.250.12.1`.

---

## 3. `lab-edge` – [`edge-hybrid.sh`](../images/edge/edge-hybrid.sh)

**Interfaces**

```text
$ ip -br addr show
wg0              UNKNOWN        10.250.0.1/16
vrf-s12          UP
eth2.112@eth2    UP             192.168.1.254/24
vrf-s7           UP
eth2.107@eth2    UP             192.168.1.254/24
vrf-s5           UP
eth2.105@eth2    UP             192.168.1.254/24
eth1             UP             10.20.99.2/30        # corporate side
eth3             UP             10.198.0.2/24        # lab management
```

| Item | Why | |
|---|---|---|
| `wg0`, UDP 51820, 10.250.0.1/16, default VRF | one port for the corporate firewall | [real] |
| per setup: `vrf-sN` (table 1000 + N) with `eth2.<vlan>` and `192.168.1.254/24` | the setups' identical networks live in separate tables; no gateway needed | [real] |
| per setup: `10.250.0.0/16 dev wg0` in table 1000 + N | replies from the setup must leave the VRF towards `wg0` | [real] |
| per setup: MASQUERADE on `eth2.<vlan>` (chain `nat-sN`) | devices have a wrong default gateway (E04) | [real] |
| per setup: `ip rule from 10.250.N.0/24 iif wg0 lookup 1000+N pref 10000` | **selects the setup**. Static: created with the setup, like the VRF | [real] |
| per booking (`edge-ctl peer add`): WireGuard peer with `allowed-ips 10.250.N.U/32` | the only per-booking change. `edge-ctl` refuses an address outside `10.250.N.0/24` | [real] |
| **safeguard 1:** `ip rule iif wg0 unreachable pref 30000` | a rule into a table without a matching route (typo, setup removed) ends here instead of falling through to the main table with the corporate routes | [real] |
| **safeguard 2:** per setup `iifname wg0 oifname eth2.<vlan> ip saddr 10.250.N.0/24 accept` | only setup N's prefix may enter setup N. A setup rule pointing to the wrong table is dropped | [real] |
| input: from `wg0` only ping to 10.250.0.1 | `wg0` is in the default VRF, so the `local` table makes 10.20.99.2 and 10.198.0.2 addressable from the tunnels (as in PBR) | [real] |

The routing rule and safeguard 2 are both written per setup from the same prefix, so a mistake in one is caught
by the other, and no booking touches either.

**Forward rules**

```text
$ nft list chain inet edge setups
        iifname "wg0" oifname "eth2.112" ip saddr 10.250.12.0/24 accept
        iifname "wg0" oifname "eth2.107" ip saddr 10.250.7.0/24 accept
        iifname "wg0" oifname "eth2.105" ip saddr 10.250.5.0/24 accept
```

Unlike the VRF lab, `oifname "eth2.112"` matches directly: the packet arrives on `wg0` (not a VRF member),
so the forward hook sees the real interfaces. Replies come from inside the VRF and are accepted by `ct state established`.

**`edge-ctl`**: `peer add` only adds the peer (as in VRF, but on `wg0`), `nat` works on the edge:

```text
$ edge-ctl status
mode hybrid
wg0 port 51820 peers 1
setup 12 table 1012 rule 10.250.12.0/24
setup 7 table 1007 rule 10.250.7.0/24
setup 5 table 1005 rule 10.250.5.0/24

$ edge-ctl peer add --setup 12 --pubkey 6PdV…Vih4= --ip 10.250.7.1
edge-ctl: --ip must be 10.250.12.<user 1-254> for setup 12 (got '10.250.7.1')
```

---

## 4. What can go wrong (E10)

| Misconfiguration | Result |
|---|---|
| Booking: address outside the setup's prefix | **refused** by `edge-ctl` |
| Booking: registered on the wrong setup (`--setup 7 --ip 10.250.7.1`), agent connects as 10.250.12.1 | **fail-closed**: the key may only send from 10.250.7.1 |
| Setup: rule missing | **fail-closed**: the catch-all rule makes it unreachable |
| Setup: rule into a table without route | **fail-closed**: catch-all, never the main table (`ip route get` → unreachable) |
| Setup: rule points to the **wrong setup's table** | **fail-closed**: dropped by safeguard 2. Control in the same test: without safeguard 2 Alice lands in setup 7 |
| A device of setup 12 (edge as gateway) towards a tunnel booked into setup 7 | blocked: only replies may go from a setup into `wg0` |
| Broker registers the wrong setup **and** tells the agent that setup's address | **foreign access**: consistent in itself. The broker's reconcile loop (stage 3) compares peers with bookings |
| Changing `wg0` (restart) | interrupts **every** user, as in PBR (VRF: one setup) |

---

## Carried over to the real environment

In addition to [common.md](common.md#4-carried-over-to-the-real-environment-all-modes):

1. One WireGuard interface and port (51820); corporate allowance: that single UDP port.
2. Tunnel addresses 10.250.\<setup\>.\<user\>; setup N owns `10.250.N.0/24` ([tunnel-addresses.md](tunnel-addresses.md)).
3. Per setup: a VRF with the setup's VLAN interface (`.254`), MASQUERADE, a route back to the tunnel network via
   `wg0`, a routing rule from the setup prefix into the VRF's table, and a forward rule for that prefix only.
4. Per booking: one WireGuard peer with a `/32` from the setup's prefix.
5. Fail-closed for a missing or empty table: on Linux the catch-all rule, on RouterOS `action=lookup-only-in-table`.
6. On RouterOS the forward rule needs a mangle mark per setup VLAN (`out-interface=vlanN` only works in mangle)
   plus `src-address=10.250.N.0/24` ([routeros-spike.md, section 6](routeros-spike.md#6-hybrid-design-on-routeros)).
