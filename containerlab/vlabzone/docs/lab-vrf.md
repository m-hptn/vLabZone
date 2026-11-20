# Lab `vlabzone-vrf` – edge with one VRF per setup

Start: `make up` (or `make up EDGE_MODE=vrf`) · Containers: `clab-vlabzone-vrf-<node>` · 13 nodes ·
Topology: [`vlabzone-vrf.clab.yml`](../topology/vlabzone-vrf.clab.yml) (generated)

The nodes that are the same in both labs (corporate firewall, workspaces, devices, broker, switch basics,
management) are described in [common.md](common.md). This document covers what makes the VRF lab what it is.

---

## 1. Overview

```text
                  corporate (simulated)                    │                     lab zone
                                                           │
 ws-std   10.20.10.11 ─┐                                   │                                ┌ eth11 dev-12-20 .20 ┐ VLAN 112
 ws-power 10.20.10.12 ─┼─ br-ws ┐                          │                                ├ eth12 dev-12-21 .21 ┘ setup 12
 ws-ci    10.20.10.13 ─┘        │                          │                                │
                             corp-fw ─ eth10 ─ UDP 51800-52099 ─ eth1 lab-edge eth2 ═ trunk 112,107 ═ eth1 lab-sw
 corp-svc 10.20.20.10 ── eth4 ─┘  └─ eth11 ─ TCP 443/8443 ─ eth1 broker   │ eth3                    │
                                                           │       │ eth2   │                         ├ eth21 dev-7-20  .20 ┐ VLAN 107
                                                           │       └──── lab-mgmt 10.198.0.0/24 ── eth2  └ eth22 dev-7-21  .21 ┘ setup 7
```

The edge is the gateway (`192.168.1.254`) in **every** setup. It has a VRF per setup, each with
its own routing table, its own VLAN interface and its own WireGuard interface:

```text
              ┌──────────────────────────── lab-edge ────────────────────────────┐
  UDP 51812 ──┤ wg-s12 ─┐                                                         │
              │         ├─ vrf-s12 (table 1012) ── eth2.112 192.168.1.254 ── VLAN 112 ── setup 12
  eth1        │         │                                                         │
  10.20.99.2  │ wg-s7  ─┐                                                         │
  UDP 51807 ──┤         ├─ vrf-s7  (table 1007) ── eth2.107 192.168.1.254 ── VLAN 107 ── setup 7
              └─────────────────────────────────────────────────────────────────────┘
```

| Node | Role here |
|---|---|
| `lab-edge` | WireGuard + VRF + VLAN + MASQUERADE per setup (section 3) |
| `lab-sw` | trunk with every setup VLAN (section 4) |
| all others | as in [common.md](common.md) |

### Addressing

| Segment | Network | Why this way |
|---|---|---|
| Workspaces | 10.20.10.0/24 | one corporate client net |
| Corporate services | 10.20.20.0/24 | a second corporate net, so isolation from it can be tested |
| corp-fw ↔ lab-edge | 10.20.99.0/30 | the **only** path for tunnel traffic |
| corp-fw ↔ broker | 10.20.98.0/30 | the **only** path for API traffic |
| Lab management | 10.198.0.0/24 | broker (.1) controls edge (.2) and switch (.3), invisible to the corporate side |
| WireGuard tunnels | 10.250.0.0/16 | edge 10.250.0.1 in every VRF; context = 10.250.\<user\>.\<setup\> |
| Setups 12 and 7 | 192.168.1.0/24 (both), VLAN 112 / 107 | deliberately the **same** network; the edge is .254 in both |

---

## 2. One request, hop by hop

Alice in `ws-std` has a tunnel to setup 12 and runs `curl http://192.168.1.20`.
Each line was captured at the named hop (TCP SYN, shortened):

| # | Hop | What is seen | What happened |
|---|---|---|---|
| 1 | `ws-std` wg-s12 | `10.250.1.12.41834 > 192.168.1.20.80 [S] mss 1380` | the route `192.168.1.0/24 dev wg-s12 src 10.250.1.12` sends it into the tunnel |
| 2 | `ws-std` eth1 | `10.20.10.11.54059 > 10.20.99.2.51812: UDP, length 96` | WireGuard encrypts it; **port 51812 selects setup 12** |
| 3 | `corp-fw` eth10 | `10.20.10.11.54059 > 10.20.99.2.51812: UDP` | the corporate side sees only encrypted UDP inside the allowed range |
| 4 | `lab-edge` wg-s12 | `10.250.1.12.41834 > 192.168.1.20.80 [S]` | decrypted. The key allows only source `10.250.1.12/32`. The packet now lives in VRF `vrf-s12` |
| 5 | `lab-edge` eth2 | `vlan 112 … 192.168.1.254.41834 > 192.168.1.20.80` | routed in table 1012 to `eth2.112`, **MASQUERADE** to 192.168.1.254, tagged VLAN 112 |
| 6 | `lab-sw` eth11 | `192.168.1.254.41834 > 192.168.1.20.80` (no tag) | the switch forwards VLAN 112 only to setup 12's access ports and removes the tag |
| 7 | `dev-12-20` eth1 | `192.168.1.254.41834 > 192.168.1.20.80` | the device sees a client on its own subnet and answers it directly |

The edge's conntrack entry shows the translation in both directions:

```text
src=10.250.1.12 dst=192.168.1.20 sport=41820 dport=80   src=192.168.1.20 dst=192.168.1.254 sport=80 dport=41820
```

The reply takes the same path backwards. The edge reverses the NAT and routes `10.250.1.12` in table 1012 into
`wg-s12`. WireGuard picks the peer whose allowed IPs contain `10.250.1.12`, encrypts the reply and sends it to
the workspace's UDP endpoint.

**Where the setup is decided:** by the **UDP port** the workspace connects to (hop 2). From then on, the packet
can only exist inside `vrf-s12`. There is no per-booking routing decision.

`mss 1380` comes from the WireGuard MTU of 1420 (1500 − 80 bytes overhead). The containerlab links use MTU 9500,
so the lab will not show MTU problems by itself; experiment E15 lowers the MTU on purpose.

---

## 3. `lab-edge` – WireGuard, VRF per setup, NAT

**Role:** The only entry into the lab zone. It ends the tunnels, puts each tunnel into the VRF of exactly one
setup and makes the setup's devices reachable under their original IPs. This is the node that will later be
the MikroTik.

**Interfaces**

| Interface | Address | Purpose |
|---|---|---|
| eth1 | 10.20.99.2/30 | corporate side, receives only WireGuard UDP |
| eth2 | – | trunk to lab-sw (VLAN 112, 107 tagged) |
| eth2.112 (in vrf-s12) | 192.168.1.254/24 | gateway in setup 12 |
| eth2.107 (in vrf-s7) | 192.168.1.254/24 | gateway in setup 7, **same address** as in setup 12 |
| wg-s12 (in vrf-s12) | 10.250.0.1/16, UDP 51812 | tunnels to setup 12 |
| wg-s7 (in vrf-s7) | 10.250.0.1/16, UDP 51807 | tunnels to setup 7 |
| eth3 | 10.198.0.2/24 | management (broker, stage 3) |

**Configuration** (`images/edge/entrypoint.sh`, `images/edge/edge-vrf.sh`)

| Setting | Why | Without it |
|---|---|---|
| One VRF per setup (`vrf-sN`, table 1000+N) **[real]** | each VRF has its own routing table, so `192.168.1.0/24` can exist once per setup. Traffic from `wg-s12` is only ever looked up in table 1012 | overlapping setups would be impossible: one routing table can hold 192.168.1.0/24 only once |
| VLAN subinterface `eth2.<vlan>` in the VRF **[real]** | binds the setup's L2 segment to its VRF | no path from the VRF into the setup |
| Same gateway IP `192.168.1.254` in every VRF **[real]** | one uniform address per setup; allowed because each VRF is separate | – |
| One WireGuard interface per setup, port 51800+N **[real]** | the port selects the setup. An interface can belong to one VRF only, so each setup needs its own | a single interface would need policy routing (that is the PBR lab) |
| WireGuard interface in the VRF, its UDP socket outside **[real]** | the encrypted UDP traffic arrives on eth1 (default VRF); the decrypted packets appear in `vrf-sN` | if the socket were in the VRF, it would never receive anything from eth1 |
| `net.ipv4.udp_l3mdev_accept` / `tcp_l3mdev_accept` left at **0** **[real]** | tested: tunnels work without it, because the WireGuard sockets only receive on eth1, outside any VRF. With 1, sockets on the edge itself would accept traffic arriving from inside a setup | services on the edge (e.g. SSH for the broker) would be reachable from the setups, with only the firewall in between |
| `ip_forward=1` **[real]** | the edge routes between tunnel and VLAN | nothing gets through |
| Only `10.20.0.0/16 via 10.20.99.1` in the main table, **no default route** **[real]** | the edge only needs to reach the corporate clients | a default route could become a path from the lab to the internet or into other networks |
| Private keys generated at start, in `/run/vlab/keys` **[sim → real]** | every start gets new keys, which is what E18 (edge restart) needs. In the real setup they must be persistent | – |
| **No peers at start** **[real]** | access exists only while the broker grants it. A restart drops all access until the broker restores it (E18) | stale access after a restart |

**Firewall and NAT** (nft table `inet edge`)

| Chain / rule | Why |
|---|---|
| `input` policy drop: `iifname eth1 udp dport 51800-52099 accept` | only WireGuard from the corporate side reaches the edge itself |
| `input`: `meta sdifname "wg-s*" ip daddr 10.250.0.1 icmp echo-request accept` | ping to the tunnel gateway for diagnosis, and **only** to that address |
| `input`: `iifname eth3 tcp dport 22 accept` | management access for the broker (used from stage 3) |
| `forward` policy drop, `ct state established,related accept` | replies only; a setup cannot open connections anywhere (E05) |
| `setups`: `meta sdifname "wg-s12" oifname "eth2.112" accept` | the only allowed new flow: tunnel of setup N → VLAN of setup N |
| `nat-sN`: `oifname "eth2.<vlan>" masquerade` | devices see `192.168.1.254` as the client and answer on their own subnet. That needs no gateway on the device and no route back to 10.250/16. A separate chain per setup lets `edge-ctl nat off` switch it off for E04 |

> **Finding (VRF + nftables) [real]:** for traffic arriving on an interface that belongs to a VRF, the
> `input` and `forward` hooks see the **VRF device** (`vrf-s12`) as `iifname`, not `wg-s12`. Rules must match
> the real ingress interface with **`meta sdifname`**. With `iifname "wg-s12"` all tunnel traffic was silently
> dropped. The real edge configuration must account for this (for RouterOS: checked in stage 1b).

**`edge-ctl`**: the single interface for granting and revoking access. The test helpers use it now, the
broker will call it over SSH from stage 3.

| Command | Effect in this lab |
|---|---|
| `edge-ctl peer add --setup N --pubkey K --ip 10.250.U.N` | `wg set wg-sN peer K allowed-ips 10.250.U.N/32`: **one** change per booking. Cryptokey routing: the key may only send from this one address (E06) |
| `edge-ctl peer del --setup N --pubkey K` | removes the peer and deletes its conntrack entries, so running sessions end at once (E08) |
| `edge-ctl pubkey` / `port --setup N` | key and port of `wg-sN`, different for every setup |
| `edge-ctl peer list`, `status` | state for tests and the later reconcile loop |
| `edge-ctl nat on\|off --setup N` | switch MASQUERADE (E04) |

It accepts only well-formed keys, known setups and addresses in 10.250.0.0/16.

**Tested by:** E01–E06, E09, E10 (VRF cases).

```bash
docker exec clab-vlabzone-vrf-lab-edge edge-ctl status
docker exec clab-vlabzone-vrf-lab-edge ip -br addr
docker exec clab-vlabzone-vrf-lab-edge ip route show vrf vrf-s12
docker exec clab-vlabzone-vrf-lab-edge wg show
docker exec clab-vlabzone-vrf-lab-edge nft list ruleset
docker exec clab-vlabzone-vrf-lab-edge conntrack -L
```

---

## 4. `lab-sw` in this lab

Basics: [common.md](common.md#22-lab-sw--vlan-switch-basics).

| Port | VLAN | Why |
|---|---|---|
| eth1 (trunk) | **112, 107** tagged | the edge is the gateway in every setup, so it needs every setup VLAN. The PBR transfer VLAN 199 is not on the trunk |
| eth11, eth12 / eth21, eth22 | 112 / 107 untagged | devices |

```text
port              vlan-id
eth1              107
                  112
eth11             112 PVID Egress Untagged
eth12             112 PVID Egress Untagged
eth21             107 PVID Egress Untagged
eth22             107 PVID Egress Untagged
```

---

## 5. What can go wrong (E10)

| Misconfiguration | Result |
|---|---|
| Booking: peer registered on the wrong setup | **fail-closed**: the key is unknown on the interface the workspace connects to, so there is no handshake |
| Forward rule for a setup missing | **fail-closed** |
| Setup: tunnel interface in the wrong VRF **and** a matching forward rule | **one-way leak**: requests reach the foreign setup; replies are routed to that setup's own WireGuard interface and dropped |

Mistakes in a single booking fail closed. Leaking takes two consistent mistakes in the setup's own
configuration, which is created once and can be checked. Details and comparison: [vrf-vs-pbr.md](vrf-vs-pbr.md).

---

## Carried over to the real environment

In addition to [common.md](common.md#4-carried-over-to-the-real-environment-both-modes):

1. Per setup: VRF + VLAN interface + WireGuard interface (own port), MASQUERADE on the setup VLAN.
2. Forward only "tunnel N → VLAN N"; no default route on the edge; `l3mdev_accept` off.
3. Firewall rules inside VRFs must match the real ingress interface (`meta sdifname` on Linux; RouterOS: stage 1b).
4. Corporate allowance: a port range (one port per setup).
