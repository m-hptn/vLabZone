# Lab `vlabzone-hybrid` – one WireGuard port, setups in VRFs on the edge

Start: `make up EDGE_MODE=hybrid` · Containers: `clab-vlabzone-hybrid-<node>` · 19 nodes ·
Topology: [`vlabzone-hybrid.clab.yml`](../topology/vlabzone-hybrid.clab.yml) (generated)

The nodes that are the same in every lab (corporate firewall, workspaces, devices, broker, switches,
management) are described in [common.md](common.md). This document covers what makes the hybrid lab what it is.

**Why this mode exists:** the corporate firewall should need **one rule with one port**. The VRF design needs
one port per setup (51800 + N), and PBR needs a gateway per setup and puts every setup's traffic on a shared
transfer VLAN. The hybrid keeps PBR's single port and VRF's setups on the edge itself:

| | VRF | PBR | **hybrid** |
|---|---|---|---|
| WireGuard on the edge | `wg-sN` per setup, port 51800 + N | one `wg0`, port 51820 | one `wg0`, port 51820 |
| Setup network on the edge | VLAN interface in `vrf-sN` | none (gateway gw-N) | VLAN interface in `vrf-sN` |
| What selects the setup | the port the workspace connects to | `ip rule` per booking → table → gw-N | `ip rule` per booking → **the VRF's table** |
| MASQUERADE | edge | gw-N | edge |

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
 UDP 51820 ──┤ wg0 10.250.0.1 (default VRF)                                                 │
 (eth1)      │  ip rule from 10.250.1.12 iif wg0 lookup 1012 ──→ vrf-s12: eth2.112 .254 ──┼── VLAN 112
             │  ip rule from 10.250.2.7  iif wg0 lookup 1007 ──→ vrf-s7:  eth2.107 .254 ──┼── VLAN 107
             │  ip rule iif wg0 unreachable (catch-all)          vrf-s5:  eth2.105 .254 ──┼── VLAN 105
             │  each VRF table also: 10.250.0.0/16 dev wg0 (way back to the tunnels)       │
             └──────────────────────────────────────────────────────────────────────────────┘
```

The switches are configured as in the VRF lab: the edge trunk carries every setup VLAN, no transfer VLAN,
no gateways (`vlab-cfg switch-config agg` with `EDGE_MODE=hybrid`).

### Addressing

As in the VRF lab ([lab-vrf.md](lab-vrf.md#addressing)), except:

| Segment | Network | Why this way |
|---|---|---|
| WireGuard tunnels | 10.250.0.0/16 on `wg0` | context = 10.250.\<user\>.\<setup\>. The edge routes on this address (rule per booking), and its **last octet is checked** against the setup (section 3) |
| corp-fw allowance | UDP **51820** only | `corp-fw.sh` writes the port per mode: VRF 51800–52099, PBR and hybrid 51820 (E01 checks it) |

---

## 2. One request

Alice in `ws-std` has a tunnel to setup 12 and runs `curl http://192.168.1.20`. Outputs from the running lab:

```text
$ ip rule show                                   # on lab-edge
0:      from all lookup local
1000:   from all lookup [l3mdev-table]
10000:  from 10.250.1.12 iif wg0 lookup 1012     # Alice's booking (edge-ctl peer add)
30000:  from all iif wg0 unreachable             # catch-all, written at start
32766:  from all lookup main

$ ip route show table 1012                       # vrf-s12
10.250.0.0/16 dev wg0 scope link                 # back to the tunnels (wg0 is outside the VRF)
192.168.1.0/24 dev eth2.112 proto kernel scope link src 192.168.1.254
local 192.168.1.254 dev eth2.112 proto kernel scope host src 192.168.1.254

$ ip route get 192.168.1.20 from 10.250.1.12 iif wg0
192.168.1.20 from 10.250.1.12 dev eth2.112 table 1012

$ ip route get 10.20.20.10 from 10.250.1.12 iif wg0     # corporate net from the tunnel
RTNETLINK answers: Network is unreachable
```

1. WireGuard decrypts on `wg0`; the key allows only source `10.250.1.12/32`.
2. The rule at pref 10000 sends the lookup into table 1012, the table of `vrf-s12`. A route from the default VRF
   into a VRF's table works in Linux for forwarded traffic: the packet leaves on `eth2.112`.
3. Forward filter: `iifname "wg0" oifname "eth2.112" ip saddr & 0.0.0.255 == 0.0.0.12 accept`.
4. MASQUERADE on `eth2.112`: the device sees `192.168.1.254`, as in the VRF lab.
5. The reply arrives on `eth2.112`, inside `vrf-s12`. Conntrack reverses the NAT, the lookup in table 1012 finds
   `10.250.0.0/16 dev wg0`, and WireGuard picks the peer whose allowed IPs contain `10.250.1.12`.

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
| per booking: `ip rule from 10.250.U.N iif wg0 lookup 1000+N pref 10000` (`edge-ctl peer add`) | selects the setup | [real] |
| **safeguard 1:** `ip rule iif wg0 unreachable pref 30000` | a rule into a table without a matching route (typo, setup removed) ends here instead of falling through to the main table with the corporate routes | [real] |
| **safeguard 2:** one forward rule per setup, `iifname wg0 oifname eth2.<vlan> ip saddr & 0.0.0.255 == N` | a tunnel may only enter setup N if its address ends in `.N`. A booking rule pointing to the wrong table is dropped | [real] |
| input: from `wg0` only ping to 10.250.0.1 | `wg0` is in the default VRF, so the `local` table makes 10.20.99.2 and 10.198.0.2 addressable from the tunnels (as in PBR) | [real] |

Both safeguards are written once at start (per setup), never per booking. So a per-booking mistake (the rule)
is caught by a per-setup check that doesn't change with bookings.

**Forward and NAT rules**

```text
$ nft list chain inet edge setups
        iifname "wg0" oifname "eth2.112" ip saddr & 0.0.0.255 == 0.0.0.12 accept
        iifname "wg0" oifname "eth2.107" ip saddr & 0.0.0.255 == 0.0.0.7 accept
        iifname "wg0" oifname "eth2.105" ip saddr & 0.0.0.255 == 0.0.0.5 accept
$ nft list chain inet edge nat-s12
        oifname "eth2.112" masquerade
```

Unlike the VRF lab, `oifname "eth2.112"` matches directly: the packet arrives on `wg0` (not a VRF member),
so the forward hook sees the real interfaces. Replies come from inside the VRF and are accepted by `ct state established`.

**`edge-ctl`** works as in PBR (peer + rule per booking), `edge-ctl nat` as in VRF (on the edge):

```text
$ edge-ctl status
mode hybrid
wg0 port 51820 peers 1
setup 12 table 1012 rules 1
setup 7 table 1007 rules 0
setup 5 table 1005 rules 0
```

---

## 4. What can go wrong (E10)

| Misconfiguration | Result |
|---|---|
| Booking: `ip rule` missing | **fail-closed**: the catch-all rule makes it unreachable |
| Booking: `ip rule` into a table without route | **fail-closed**: catch-all, never the main table (`ip route get` → unreachable) |
| Booking: `ip rule` points to the **wrong setup's table** | **fail-closed**: dropped by safeguard 2. Control in the same test: without safeguard 2 Alice lands in setup 7 (as in PBR) |
| A device of setup 12 (edge as gateway) towards a tunnel booked into setup 7 | blocked: only replies may go from a setup into `wg0` |
| Broker assigns the wrong **tunnel address** (10.250.1.7 for a setup-12 booking) together with a matching rule | **foreign access**: address and rule agree, safeguard 2 can't tell. This is the remaining per-booking risk; the reconcile loop (stage 3) must compare addresses with bookings |
| Changing `wg0` (restart) | interrupts **every** user, as in PBR (VRF: one setup) |

Test results (`make test EDGE_MODE=hybrid`): **63 passed**, 6 skipped (the PBR- and VRF-specific E10 cases,
and the RouterOS-only check).

---

## Carried over to the real environment

In addition to [common.md](common.md#4-carried-over-to-the-real-environment-all-modes):

1. One WireGuard interface and port (51820); corporate allowance: that single UDP port.
2. Per setup: a VRF with the setup's VLAN interface (`.254`), MASQUERADE, and a route back to the tunnel network via `wg0`.
3. Per booking: peer + routing rule into the setup's VRF table.
4. Fail-closed for a missing or empty table: on Linux the catch-all rule, on RouterOS `action=lookup-only-in-table`.
5. Safeguard against a wrong rule. On Linux: nftables mask on the last octet. **RouterOS cannot match the last
   octet** ([routeros-spike.md, section 6](routeros-spike.md#6-hybrid-design-on-routeros)): there the safeguard
   needs tunnel addresses grouped by setup (10.250.\<setup\>.\<user\>). With that scheme the per-booking rule
   disappears as well (one static rule per setup).
