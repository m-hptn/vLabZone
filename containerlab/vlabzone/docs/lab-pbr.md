# Lab `vlabzone-pbr` – edge with policy-based routing and a gateway per setup

Start: `make up EDGE_MODE=pbr` · Containers: `clab-vlabzone-pbr-<node>` · 15 nodes ·
Topology: [`vlabzone-pbr.clab.yml`](../topology/vlabzone-pbr.clab.yml) (generated)

The nodes that are the same in both labs (corporate firewall, workspaces, devices, broker, switch basics,
management) are described in [common.md](common.md). This document covers what makes the PBR lab what it is.

---

## 1. Overview

```text
                  corporate (simulated)                    │                          lab zone
                                                           │
 ws-std   10.20.10.11 ─┐                                   │                     ┌ eth19 ─ gw-12 ─ eth18 ┐
 ws-power 10.20.10.12 ─┼─ br-ws ┐                          │                     │  (VLAN 199)  (VLAN 112)├ eth11 dev-12-20 .20 ┐ setup 12
 ws-ci    10.20.10.13 ─┘        │                          │                     │                        └ eth12 dev-12-21 .21 ┘ VLAN 112
                             corp-fw ─ eth10 ─ UDP 51820 ─ eth1 lab-edge eth2 ═ trunk 199 ═ eth1 lab-sw
 corp-svc 10.20.20.10 ── eth4 ─┘  └─ eth11 ─ TCP 443/8443 ─ eth1 broker   │ eth3       │
                                                           │       │ eth2   │          └ eth29 ─ gw-7 ── eth28 ┐
                                                           │       └──── lab-mgmt ── eth2    (VLAN 199)  (VLAN 107)├ eth21 dev-7-20 .20 ┐ setup 7
                                                           │          10.198.0.0/24                           └ eth22 dev-7-21 .21 ┘ VLAN 107
```

The edge has **no interface in any setup**. It has one WireGuard interface for all tunnels and
decides per tunnel source address which setup gateway a packet goes to:

```text
              ┌──────────────────────── lab-edge ────────────────────────┐
  UDP 51820 ──┤ wg0 10.250.0.1                                           │
  eth1        │   │  ip rule from 10.250.1.12 iif wg0 → table 1012 ─┐    │
  10.20.99.2  │   │  ip rule from 10.250.2.7  iif wg0 → table 1007 ─┼─ eth2.199 10.199.0.1 ── VLAN 199 ─┬─ gw-12 10.199.0.12 ─ 192.168.1.254 ─ VLAN 112 ─ setup 12
              │   │  (no rule: main table, no route into setups)    │    │                              └─ gw-7  10.199.0.7  ─ 192.168.1.254 ─ VLAN 107 ─ setup 7
              │   table 1012: default via 10.199.0.12               │    │
              │   table 1007: default via 10.199.0.7  ──────────────┘    │
              └──────────────────────────────────────────────────────────┘
```

| Node | Role here |
|---|---|
| `lab-edge` | one `wg0`, an `ip rule` per booking, a routing table per setup (section 3) |
| `gw-12`, `gw-7` | setup gateways: `.254` in the setup, MASQUERADE (section 4) |
| `lab-sw` | trunk only with the transfer VLAN, extra ports for the gateways (section 5) |
| all others | as in [common.md](common.md) |

### Addressing

| Segment | Network | Why this way |
|---|---|---|
| Workspaces | 10.20.10.0/24 | one corporate client net |
| Corporate services | 10.20.20.0/24 | a second corporate net, so isolation from it can be tested |
| corp-fw ↔ lab-edge | 10.20.99.0/30 | the **only** path for tunnel traffic |
| corp-fw ↔ broker | 10.20.98.0/30 | the **only** path for API traffic |
| Lab management | 10.198.0.0/24 | broker (.1) controls edge (.2) and switch (.3), invisible to the corporate side |
| WireGuard tunnels | 10.250.0.0/16 | edge 10.250.0.1 on `wg0`; context = 10.250.\<user\>.\<setup\>. **This address is what the edge routes on** |
| Transfer | 10.199.0.0/24, VLAN 199 | edge .1, gateway of setup N = .N (`vlab-cfg gw-ip N`) |
| Setups 12 and 7 | 192.168.1.0/24 (both), VLAN 112 / 107 | deliberately the **same** network; the setup's gateway gw-N is .254 |

---

## 2. One request, hop by hop

Alice in `ws-std` has a tunnel to setup 12 and runs `curl http://192.168.1.20`.
Each line was captured at the named hop (TCP SYN, shortened):

| # | Hop | What is seen | What happened |
|---|---|---|---|
| 1 | `ws-std` wg-s12 | `10.250.1.12.52146 > 192.168.1.20.80 [S] mss 1380` | the route `192.168.1.0/24 dev wg-s12 src 10.250.1.12` sends it into the tunnel |
| 2 | `ws-std` eth1 | `10.20.10.11.59283 > 10.20.99.2.51820: UDP, length 96` | WireGuard encrypts it. **Same port 51820 for every setup** |
| 3 | `corp-fw` eth10 | `10.20.10.11.59283 > 10.20.99.2.51820: UDP` | the corporate side sees only encrypted UDP to one port |
| 4 | `lab-edge` wg0 | `10.250.1.12.52146 > 192.168.1.20.80 [S]` | decrypted. The key allows only source `10.250.1.12/32` |
| 5 | `lab-edge` eth2 | `vlan 199 … 10.250.1.12.52146 > 192.168.1.20.80` | `ip rule from 10.250.1.12 iif wg0 lookup 1012` → `default via 10.199.0.12`. Sent into the **transfer** VLAN 199, **not translated**: the tunnel address is still the source |
| 6 | `lab-sw` eth19 | `10.250.1.12.52146 > 192.168.1.20.80` (no tag) | VLAN 199 access port of gw-12 |
| 7 | `gw-12` eth1 | `10.250.1.12.52146 > 192.168.1.20.80` | the gateway receives the tunnel address |
| 8 | `gw-12` eth2 | `192.168.1.254.52146 > 192.168.1.20.80` | **MASQUERADE here**, on the gateway: source becomes `192.168.1.254` |
| 9 | `lab-sw` eth11 | `192.168.1.254.52146 > 192.168.1.20.80` | VLAN 112, setup 12's access port |
| 10 | `dev-12-20` eth1 | `192.168.1.254.52146 > 192.168.1.20.80` | the device sees exactly what it sees in the VRF lab |

The edge's routing decision for Alice, as the kernel reports it:

```text
$ ip route get 192.168.1.20 from 10.250.1.12 iif wg0
192.168.1.20 from 10.250.1.12 via 10.199.0.12 dev eth2.199 table 1012
```

The edge's conntrack entry shows **no translation**; that happens on gw-12:

```text
src=10.250.1.12 dst=192.168.1.20 sport=54560 dport=80   src=192.168.1.20 dst=10.250.1.12 sport=80 dport=54560
```

The reply: the device answers `.254` (gw-12). gw-12 reverses the NAT and routes `10.250.1.12` via
`10.199.0.1` back to the edge. The edge finds `10.250.0.0/16 dev wg0` in its main table, and WireGuard picks
the peer whose allowed IPs contain `10.250.1.12`.

**Where the setup is decided:** by the **`ip rule`** for the tunnel source address (hop 5). That rule is
created per booking. If it points to the wrong table, the packet goes to the wrong gateway (section 6).

---

## 3. `lab-edge` – one WireGuard interface, policy routing

**Role:** The only entry into the lab zone, as in the VRF lab. Here it does not know the setups' networks
at all; it only decides which gateway a tunnel's traffic goes to.

**Interfaces**

| Interface | Address | Purpose |
|---|---|---|
| eth1 | 10.20.99.2/30 | corporate side, receives only WireGuard UDP 51820 |
| eth2 | – | trunk to lab-sw (VLAN 199 tagged only) |
| eth2.199 | 10.199.0.1/24 | transfer VLAN to the gateways gw-N (10.199.0.N) |
| wg0 | 10.250.0.1/16, UDP **51820** | all tunnels, all setups |
| eth3 | 10.198.0.2/24 | management (broker, stage 3) |

**Routing**

```text
$ ip rule show
0:      from all lookup local
10000:  from 10.250.1.12 iif wg0 lookup 1012      ← created by edge-ctl peer add (per booking)
32766:  from all lookup main
32767:  from all lookup default

$ ip route show table 1012
default via 10.199.0.12 dev eth2.199

$ ip route                                         (main table – no route into any setup)
10.20.0.0/16 via 10.20.99.1 dev eth1
10.20.99.0/30 dev eth1 proto kernel scope link src 10.20.99.2
10.198.0.0/24 dev eth3 proto kernel scope link src 10.198.0.2
10.199.0.0/24 dev eth2.199 proto kernel scope link src 10.199.0.1
10.250.0.0/16 dev wg0 proto kernel scope link src 10.250.0.1
```

**Configuration** (`images/edge/entrypoint.sh`, `images/edge/edge-pbr.sh`)

| Setting | Why | Without it |
|---|---|---|
| One `wg0` on port 51820 **[real]** | one port for every setup; the corporate allowance could shrink to this single port | – |
| Table `1000+N` per setup with only `default via 10.199.0.N` **[real]** | everything a context sends goes to its setup's gateway and nowhere else | – |
| Per booking: `ip rule from 10.250.U.N iif wg0 lookup 1000+N pref 10000` **[real]** | **the** isolation mechanism of PBR: the tunnel source address picks the table. `/32` allowed IPs per key make sure the address can't be faked (E06) | the main table is used, which has no route into any setup: no access (tested, fail-closed) |
| `iif wg0` in every rule **[real]** | the rule only applies to packets that really came out of the tunnel, where WireGuard has checked the source address | tested: a packet with a forged source `10.250.1.12` arriving from the transfer VLAN would be routed into setup 12's table. With `iif wg0`: "network unreachable" |
| Main table without routes into setups **[real]** | a missing rule must not lead anywhere | a missing rule would leak into whichever setup the main table points to |
| No VRF, no setup VLAN, no NAT on the edge **[real]** | the setup side lives entirely on the gateways | – |
| `ip_forward=1`, no default route, keys generated at start, no peers at start | as in the VRF lab ([lab-vrf.md](lab-vrf.md#3-lab-edge--wireguard-vrf-per-setup-nat)) | |

**Firewall** (nft table `inet edge`)

| Chain / rule | Why |
|---|---|
| `input` policy drop: `iifname eth1 udp dport 51820 accept` | only WireGuard from the corporate side reaches the edge itself |
| `input`: `iifname wg0 ip daddr 10.250.0.1 icmp echo-request accept` | ping to the tunnel gateway, **and only to that address**. The `local` routing table is consulted before every `ip rule`, so all edge addresses (10.199.0.1, 10.198.0.2, …) can be addressed from a tunnel. E10 found the edge answering pings on them before the destination was restricted |
| `input`: `iifname eth3 tcp dport 22 accept` | management access for the broker (used from stage 3) |
| `forward` policy drop, `ct state established,related accept` | replies only |
| `forward`: `iifname wg0 oifname eth2.199 accept` | tunnels may go into the transfer VLAN. **Which** gateway is decided by the ip rules, not by the firewall |

There is no NAT on the edge in this lab.

**`edge-ctl`**: same commands as in the VRF lab, different effect:

| Command | Effect in this lab |
|---|---|
| `edge-ctl peer add --setup N --pubkey K --ip 10.250.U.N` | `wg set wg0 peer K allowed-ips 10.250.U.N/32` **and** `ip rule add from 10.250.U.N iif wg0 lookup 1000+N`: **two** changes per booking |
| `edge-ctl peer del --setup N --pubkey K` | removes peer and rule, deletes conntrack entries (E08) |
| `edge-ctl pubkey` / `port --setup N` | key and port of `wg0`, the same for every setup |
| `edge-ctl peer list` | the setup column comes from the ip rules (`-` = peer without a rule) |
| `edge-ctl status` | wg0 peers and number of rules per setup table |
| `edge-ctl nat …` | refused: NAT runs on the gateways (`gw-ctl`) |

**Tested by:** E01–E06, E09 (same tests as the VRF lab), E10 (PBR cases).

```bash
docker exec clab-vlabzone-pbr-lab-edge edge-ctl status
docker exec clab-vlabzone-pbr-lab-edge edge-ctl peer list
docker exec clab-vlabzone-pbr-lab-edge ip rule show
docker exec clab-vlabzone-pbr-lab-edge ip route show table 1012
docker exec clab-vlabzone-pbr-lab-edge nft list ruleset
docker exec clab-vlabzone-pbr-lab-edge conntrack -L
```

---

## 4. `gw-12`, `gw-7` – setup gateways

**Role:** Connect the transfer VLAN to one setup. They take over what the VRF interfaces do on the VRF edge:
the setup's gateway address and the MASQUERADE.

**Interfaces** (router image, `ROLE=gw`, `SETUP=N`, `images/router/roles/gw.sh`)

| Interface | Address | Switch port |
|---|---|---|
| eth1 | 10.199.0.N/24 (transfer) | VLAN 199 (gw-12: eth19, gw-7: eth29) |
| eth2 | 192.168.1.254/24 (setup gateway) | the setup's VLAN (gw-12: eth18 / 112, gw-7: eth28 / 107) |

```text
$ ip route        (gw-12)
10.199.0.0/24 dev eth1 proto kernel scope link src 10.199.0.12
10.250.0.0/16 via 10.199.0.1 dev eth1
192.168.1.0/24 dev eth2 proto kernel scope link src 192.168.1.254
```

| Setting | Why | Without it |
|---|---|---|
| Route `10.250.0.0/16 via 10.199.0.1`, **no default route** **[real]** | replies to the tunnels go back to the edge; nothing else is reachable from the setup | a default route would be a way out of the setup |
| Forward only `eth1 → eth2` from `10.250.0.0/16`, replies via conntrack **[real]** | only tunnel traffic enters the setup. Nothing goes from the setup into the transfer VLAN | all gateways share VLAN 199: a setup could reach the other setups' gateways and the edge (E10 checks it can't) |
| No forwarding `eth1 → eth1` **[real]** | a tunnel routed to this gateway cannot be bounced on to another gateway in VLAN 199 | a context of setup 12 could reach gw-7 through gw-12 |
| MASQUERADE on eth2, in its own chain **[real]** | same reason as on the VRF edge: devices with a wrong gateway answer `.254` (E04). `gw-ctl nat on\|off` switches it | no answers from devices with a wrong gateway |
| Input: only ping from the edge (10.199.0.1) **[real]** | the gateway is diagnosable from the edge but not reachable from the setup or the tunnels | – |

> **Note [real]:** all gateways share VLAN 199 and see the tunnel addresses. Separation between setups on
> the transfer side therefore rests on each gateway's firewall. In the VRF lab, there is no shared
> segment between setups at all.

```bash
docker exec clab-vlabzone-pbr-gw-12 gw-ctl status
docker exec clab-vlabzone-pbr-gw-12 ip route
docker exec clab-vlabzone-pbr-gw-12 nft list ruleset
```

---

## 5. `lab-sw` in this lab

Basics: [common.md](common.md#22-lab-sw--vlan-switch-basics).

| Port | VLAN | Why |
|---|---|---|
| eth1 (trunk) | **199** tagged only | the edge only talks to the gateways. It gets **no** setup VLAN, so it has no direct way into any setup |
| eth19, eth29 | 199 untagged | transfer ports of gw-12 and gw-7 |
| eth18, eth28 | 112 / 107 untagged | setup ports of gw-12 and gw-7 |
| eth11, eth12 / eth21, eth22 | 112 / 107 untagged | devices |

```text
port              vlan-id
eth1              199
eth11             112 PVID Egress Untagged
eth12             112 PVID Egress Untagged
eth19             199 PVID Egress Untagged
eth18             112 PVID Egress Untagged
eth21             107 PVID Egress Untagged
eth22             107 PVID Egress Untagged
eth29             199 PVID Egress Untagged
eth28             107 PVID Egress Untagged
```

---

## 6. What can go wrong (E10)

| Misconfiguration | Result |
|---|---|
| Booking: `ip rule` missing | **fail-closed**: main table, no route into any setup |
| Booking: `ip rule` points to the wrong table | **access to the wrong setup, both ways**: Alice booked setup 12 and gets answers from setup 7 |
| Gateway: setup traffic towards the transfer VLAN | blocked by the gateway firewall (tested: tunnel and setup 12 cannot reach gw-7 or the edge) |
| Edge: tunnels addressing other edge addresses | possible through the `local` table; now restricted by the input rule (ping only to 10.250.0.1) |

Every booking creates a routing rule, and every one of them can put a user into a foreign setup. That
makes the broker's reconcile loop (stage 3) a security function here: it must compare every rule with
the bookings and repair or remove deviations. Details and comparison: [vrf-vs-pbr.md](vrf-vs-pbr.md).

---

## Carried over to the real environment

In addition to [common.md](common.md#4-carried-over-to-the-real-environment-both-modes):

1. One WireGuard interface and port (51820); corporate allowance: that single UDP port.
2. Per setup: a routing table with only `default via <gateway>`, and a gateway with `.254`, MASQUERADE, no default route.
3. Per booking: `ip rule from <tunnel IP> iif <wg> lookup <setup table>`; the main table must have no route into any setup.
4. The broker must verify every rule against the bookings (reconcile), because a wrong table gives access to a foreign setup.
5. Gateways: forward only transfer → setup for tunnel addresses, nothing back into the transfer VLAN.
6. The edge firewall must restrict tunnel traffic to the edge itself by destination; with PBR, every edge address is otherwise addressable from the tunnels.
