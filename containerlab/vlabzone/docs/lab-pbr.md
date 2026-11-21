# Lab `vlabzone-pbr` – edge with policy-based routing and a gateway per setup

Start: `make up EDGE_MODE=pbr` · Containers: `clab-vlabzone-pbr-<node>` · 22 nodes ·
Topology: [`vlabzone-pbr.clab.yml`](../topology/vlabzone-pbr.clab.yml) (generated)

The nodes that are the same in both labs (corporate firewall, workspaces, devices, broker, switches,
management) are described in [common.md](common.md). This document covers what makes the PBR lab what it is.

---

## 1. Overview

Modelled on the real setup (`device_arc.drawio`): the edge (later the MikroTik) and the Proxmox nodes hang
on the aggregation switch, which connects the system switches. A **setup is a group of devices on one system
switch**; several setups share a switch, and devices on one switch may have the same IP. In this lab the
per-setup gateways `gw-N` sit on the aggregation switch with one trunk each, the way a VM on a Proxmox node would.

```text
                  corporate (simulated)                    │                            lab zone
                                                           │
 ws-std   10.20.10.11 ─┐                                   │                               ┌ eth11 sys1-dev1 .20 ┐ setup 12
 ws-power 10.20.10.12 ─┼─ br-ws ┐                          │                               ├ eth12 sys1-dev2 .21 ┘ VLAN 112
 ws-ci    10.20.10.13 ─┘        │                          │            ┌═ eth11 ═ lab-sys1 ┼ eth13 sys1-dev3 .20 ┐ setup 7
                             corp-fw ─ UDP 51820 ─ lab-edge ═ 199 ═ lab-agg                 └ eth14 sys1-dev4 .22 ┘ VLAN 107
 corp-svc 10.20.20.10 ── eth4 ─┘  └─ TCP 443/8443 ─ broker         ║║║  │
                                                           │       ║║║  └═ eth12 ═ lab-sys2 ┬ eth11 sys2-dev1 .20 ┐ setup 5
                                                           │  eth21 ║║║ eth23                ├ eth12 sys2-dev2 .21 ┘ VLAN 105
                                                           │  gw-12 gw-7 gw-5               ├ eth13 sys2-dev3 .22   ungrouped
                                                           │  (trunk: 199 + setup VLAN)     └ eth14 sys2-dev4 .20   ungrouped
                                                           │  lab-mgmt 10.198.0.0/24: edge, broker, agg, sys1, sys2
```

The edge has **no interface in any setup**. It has one WireGuard interface for all tunnels and
decides per tunnel source address which setup gateway a packet goes to:

```text
             ┌──────────────────────────── lab-edge ────────────────────────────┐
 UDP 51820 ──┤ wg0 10.250.0.1                                                    │
 (eth1)      │  ip rule from 10.250.12.1 iif wg0 → table 1012 → via 10.199.0.12 ─┼─┐
             │  ip rule from 10.250.7.2  iif wg0 → table 1007 → via 10.199.0.7  ─┼─┤ eth2.199 10.199.0.1
             │  (no rule → main table: no route into any setup)                  │ │ VLAN 199
             └───────────────────────────────────────────────────────────────────┘ │
     ┌─────────────────────────────────────────────────────────────────────────────┘
     ├─ gw-12  10.199.0.12 │ 192.168.1.254 ─ VLAN 112 ─ setup 12 (sys1)
     ├─ gw-7   10.199.0.7  │ 192.168.1.254 ─ VLAN 107 ─ setup 7  (sys1)
     └─ gw-5   10.199.0.5  │ 192.168.1.254 ─ VLAN 105 ─ setup 5  (sys2)
```

| Node | Role here |
|---|---|
| `lab-edge` | one `wg0`, an `ip rule` per booking, a routing table per setup (section 3) |
| `gw-12`, `gw-7`, `gw-5` | setup gateways: `.254` in the setup, MASQUERADE (section 4) |
| `lab-agg`, `lab-sys1`, `lab-sys2` | edge trunk with only VLAN 199; gateway trunks; setup VLANs only towards gateways and system switches (section 5) |
| all others | as in [common.md](common.md) |

### Addressing

| Segment | Network | Why this way |
|---|---|---|
| Workspaces | 10.20.10.0/24 | one corporate client net |
| Corporate services | 10.20.20.0/24 | a second corporate net, so isolation from it can be tested |
| corp-fw ↔ lab-edge | 10.20.99.0/30 | the **only** path for tunnel traffic |
| corp-fw ↔ broker | 10.20.98.0/30 | the **only** path for API traffic |
| Lab management | 10.198.0.0/24 | broker .1, edge .2, lab-agg .3, lab-sys1 .4, lab-sys2 .5 – invisible to the corporate side |
| WireGuard tunnels | 10.250.0.0/16 | edge 10.250.0.1 on `wg0`; context = 10.250.\<setup\>.\<user\> ([tunnel-addresses.md](tunnel-addresses.md)). **This address is what the edge routes on** |
| Transfer | 10.199.0.0/24, VLAN 199 | edge .1, gateway of setup N = .N (`vlab-cfg gw-ip N`) |
| Setups 12, 7, 5 | 192.168.1.0/24 (all), VLAN 112 / 107 / 105 | deliberately the **same** network; each setup's gateway gw-N is .254. 12 and 7 share system switch sys1 |

---

## 2. One request, hop by hop

Alice in `ws-std` has a tunnel to setup 12 and runs `curl http://192.168.1.20`. In setup 12 that address
belongs to `sys1-dev1`. `sys1-dev3`, on the same switch, has the same address but is in setup 7.
Each line was captured at the named hop (TCP SYN, shortened):

| # | Hop | What is seen | What happened |
|---|---|---|---|
| 1 | `ws-std` wg-s12 | `10.250.1.12.45574 > 192.168.1.20.80` | the route `192.168.1.0/24 dev wg-s12 src 10.250.12.1` sends it into the tunnel |
| 2 | `ws-std` eth1 | `10.20.10.11.56682 > 10.20.99.2.51820: UDP, length 96` | WireGuard encrypts it. **Same port 51820 for every setup** |
| 3 | `corp-fw` eth10 | `10.20.10.11.56682 > 10.20.99.2.51820: UDP` | the corporate side sees only encrypted UDP to one port |
| 4 | `lab-edge` wg0 | `10.250.1.12.45574 > 192.168.1.20.80` | decrypted. The key allows only source `10.250.12.1/32` |
| 5 | `lab-edge` eth2 | `vlan 199 … 10.250.1.12.45574 > 192.168.1.20.80` | `ip rule from 10.250.12.1 iif wg0 lookup 1012` → `default via 10.199.0.12`. Into the **transfer** VLAN 199, **not translated**: the tunnel address is still the source |
| 6 | `lab-agg` eth1 → eth21 | `vlan 199 … 10.250.1.12.45574 > 192.168.1.20.80` | to gw-12's trunk port (the gateway's MAC was learned in VLAN 199 there) |
| 7 | `gw-12` eth1.199 | `10.250.1.12.45574 > 192.168.1.20.80` | the gateway receives the tunnel address |
| 8 | `gw-12` eth1.112 | `192.168.1.254.45574 > 192.168.1.20.80` | **MASQUERADE here**, on the gateway: source becomes `192.168.1.254` |
| 9 | `lab-agg` eth21 → eth11 | `vlan 112 … 192.168.1.254.45574 > 192.168.1.20.80` | back out of the **same** trunk port, now in VLAN 112, on to sys1 |
| 10 | `lab-sys1` eth1 → eth11 | `vlan 112 …` → untagged | only access ports in VLAN 112 get it. eth13 (sys1-dev3, same IP, VLAN 107) does not |
| 11 | `sys1-dev1` eth1 | `192.168.1.254.45574 > 192.168.1.20.80` | the device sees exactly what it sees in the VRF lab |

The edge's routing decision for Alice, as the kernel reports it:

```text
$ ip route get 192.168.1.20 from 10.250.12.1 iif wg0
192.168.1.20 from 10.250.12.1 via 10.199.0.12 dev eth2.199 table 1012
```

The edge's conntrack entry shows **no translation**; that happens on gw-12:

```text
src=10.250.12.1 dst=192.168.1.20 sport=49174 dport=80   src=192.168.1.20 dst=10.250.12.1 sport=80 dport=49174
```

The reply: the device answers `.254` (gw-12). gw-12 reverses the NAT and routes `10.250.12.1` via
`10.199.0.1` back to the edge. The edge finds `10.250.0.0/16 dev wg0` in its main table, and WireGuard picks
the peer whose allowed IPs contain `10.250.12.1`.

**Where the setup is decided:** by the **`ip rule`** for the tunnel source address (hop 5), created per booking.
If it points to the wrong table, the packet goes to the wrong gateway (section 6). On the switches, the VLAN of
the access port decides which devices are in the setup (hop 10).

---

## 3. `lab-edge` – one WireGuard interface, policy routing

**Role:** The only entry into the lab zone, as in the VRF lab. Here it does not know the setups' networks
at all; it only decides which gateway a tunnel's traffic goes to.

**Interfaces**

| Interface | Address | Purpose |
|---|---|---|
| eth1 | 10.20.99.2/30 | corporate side, receives only WireGuard UDP 51820 |
| eth2 | – | trunk to lab-agg (VLAN 199 and 197 tagged, no setup VLAN) |
| eth2.197 | 10.197.0.1/24 | appliance VLAN (NPort, USB hub): `ip rule to 10.197.0.0/24 iif wg0 lookup main` before the booking rules ([consoles-usb.md](consoles-usb.md)) |
| eth2.199 | 10.199.0.1/24 | transfer VLAN to the gateways gw-N (10.199.0.N) |
| wg0 | 10.250.0.1/16, UDP **51820** | all tunnels, all setups |
| eth3 | 10.198.0.2/24 | management (broker, stage 3) |

**Routing**

```text
$ ip rule show
0:      from all lookup local
10000:  from 10.250.12.1 iif wg0 lookup 1012      ← created by edge-ctl peer add (per booking)
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
| Per booking: `ip rule from 10.250.N.U iif wg0 lookup 1000+N pref 10000` **[real]** | **the** isolation mechanism of PBR: the tunnel source address picks the table. `/32` allowed IPs per key make sure the address can't be faked (E06) | the main table is used, which has no route into any setup: no access (tested, fail-closed) |
| `iif wg0` in every rule **[real]** | the rule only applies to packets that really came out of the tunnel, where WireGuard has checked the source address | tested: a packet with a forged source `10.250.12.1` arriving from the transfer VLAN would be routed into setup 12's table. With `iif wg0`: "network unreachable" |
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
| `edge-ctl peer add --setup N --pubkey K --ip 10.250.N.U` | `wg set wg0 peer K allowed-ips 10.250.N.U/32` **and** `ip rule add from 10.250.N.U iif wg0 lookup 1000+N`: **two** changes per booking. An address outside 10.250.N.0/24 is refused |
| `edge-ctl peer del --setup N --pubkey K` | removes peer and rule, deletes conntrack entries (E08) |
| `edge-ctl pubkey` / `port --setup N` | key and port of `wg0`, the same for every setup |
| `edge-ctl peer list` | the setup column comes from the ip rules (`-` = peer without a rule) |
| `edge-ctl status` | wg0 peers and number of rules per setup table |
| `edge-ctl nat …` | refused: NAT runs on the gateways (`gw-ctl`) |

**Tested by:** E01–E06, E09 (same tests as the VRF lab), E10 (PBR cases), E20.

```bash
docker exec clab-vlabzone-pbr-lab-edge edge-ctl status
docker exec clab-vlabzone-pbr-lab-edge edge-ctl peer list
docker exec clab-vlabzone-pbr-lab-edge ip rule show
docker exec clab-vlabzone-pbr-lab-edge ip route show table 1012
docker exec clab-vlabzone-pbr-lab-edge nft list ruleset
docker exec clab-vlabzone-pbr-lab-edge conntrack -L
```

---

## 4. `gw-12`, `gw-7`, `gw-5` – setup gateways

**Role:** Connect the transfer VLAN to one setup. They take over what the VRF interfaces do on the VRF edge:
the setup's gateway address and the MASQUERADE. In the real environment this is a candidate for a small VM or
container on a Proxmox node (or a Proxmox SDN gateway), which is why it hangs on the aggregation switch.

**Interfaces** (router image, `ROLE=gw`, `SETUP=N`, `images/router/roles/gw.sh`)

| Interface | Address | Switch side |
|---|---|---|
| eth1 | – | **one trunk** to lab-agg (gw-12: eth21, gw-7: eth22, gw-5: eth23) carrying VLAN 199 + the setup's VLAN |
| eth1.199 | 10.199.0.N/24 (transfer) | VLAN 199 |
| eth1.\<setup VLAN\> | 192.168.1.254/24 (setup gateway) | VLAN 112 / 107 / 105 |

```text
$ ip route        (gw-12)
10.199.0.0/24 dev eth1.199 proto kernel scope link src 10.199.0.12
10.250.0.0/16 via 10.199.0.1 dev eth1.199
192.168.1.0/24 dev eth1.112 proto kernel scope link src 192.168.1.254
```

| Setting | Why | Without it |
|---|---|---|
| One trunk with two VLAN interfaces **[real]** | the same way a VM on a Proxmox node would be attached: one virtual NIC on a VLAN-aware bridge, or two tagged NICs | – |
| Route `10.250.0.0/16 via 10.199.0.1`, **no default route** **[real]** | replies to the tunnels go back to the edge; nothing else is reachable from the setup | a default route would be a way out of the setup |
| Forward only transfer → setup (`eth1.199 → eth1.<vlan>`) from `10.250.0.0/16`, replies via conntrack **[real]** | only tunnel traffic enters the setup. Nothing goes from the setup into the transfer VLAN | all gateways share VLAN 199: a setup could reach the other setups' gateways and the edge (E10 checks it can't) |
| No forwarding transfer → transfer **[real]** | a tunnel routed to this gateway cannot be bounced on to another gateway in VLAN 199 | a context of setup 12 could reach gw-7 through gw-12 |
| MASQUERADE on the setup interface, in its own chain **[real]** | same reason as on the VRF edge: devices with a wrong gateway answer `.254` (E04). `gw-ctl nat on\|off` switches it | no answers from devices with a wrong gateway |
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

## 5. Switches in this lab

Basics: [common.md](common.md#22-lab-agg-lab-sys1-lab-sys2--switches). In the PBR lab the edge only talks
to the gateways: its trunk carries **only VLAN 199** and **no** setup VLAN, so the edge has no direct way
into any setup. Each gateway's trunk carries VLAN 199 plus exactly its own setup VLAN
(`bridge vlan show`, condensed):

```text
lab-agg                      lab-sys1                                 lab-sys2
eth1   199          ← edge   eth1   107 112  ← uplink                 eth1   105  ← uplink
eth11  107 112      → sys1   eth11  112 PVID untagged  sys1-dev1      eth11  105 PVID untagged  sys2-dev1
eth12  105          → sys2   eth12  112 PVID untagged  sys1-dev2      eth12  105 PVID untagged  sys2-dev2
eth21  112 199      → gw-12  eth13  107 PVID untagged  sys1-dev3      eth13  –  (ungrouped)     sys2-dev3
eth22  107 199      → gw-7   eth14  107 PVID untagged  sys1-dev4      eth14  –  (ungrouped)     sys2-dev4
eth23  105 199      → gw-5
```

The system switches are configured exactly as in the VRF lab. E02c checks every trunk against `vlab-cfg switch-config`.

---

## 6. What can go wrong (E10, E20)

| Misconfiguration | Result |
|---|---|
| Booking: `ip rule` missing | **fail-closed**: main table, no route into any setup |
| Booking: `ip rule` points to the wrong table | **access to the wrong setup, both ways**: Alice booked setup 12 and gets answers from setup 7, the neighbouring setup on the same system switch |
| Gateway: setup traffic towards the transfer VLAN | blocked by the gateway firewall (tested: tunnel and setup 12 cannot reach gw-7 or the edge) |
| Edge: tunnels addressing other edge addresses | possible through the `local` table; now restricted by the input rule (ping only to 10.250.0.1) |
| Grouping: device moved to another setup on its switch | works with one `sw-port` call; the gateways must forget the old ARP entry for the change to take effect at once |
| Grouping: two devices with the same IP in one setup | **both answer ARP**; which one gets the traffic is random. The broker must refuse it |

Every booking creates a routing rule, and every one of them can put a user into a foreign setup. That
makes the broker's reconcile loop (stage 3) a security function here: it must compare every rule with
the bookings and repair or remove deviations. Details and comparison: [vrf-vs-pbr.md](vrf-vs-pbr.md).

---

## Carried over to the real environment

In addition to [common.md](common.md#4-carried-over-to-the-real-environment-all-modes):

1. One WireGuard interface and port (51820); corporate allowance: that single UDP port.
2. Per setup: a routing table with only `default via <gateway>`, and a gateway with `.254`, MASQUERADE, no default route.
3. Per booking: `ip rule from <tunnel IP> iif <wg> lookup <setup table>`; the main table must have no route into any setup.
4. The broker must verify every rule against the bookings (reconcile), because a wrong table gives access to a foreign setup.
5. Gateways: forward only transfer → setup for tunnel addresses, nothing back into the transfer VLAN.
6. The edge firewall must restrict tunnel traffic to the edge itself by destination; with PBR, every edge address is otherwise addressable from the tunnels.
7. The MikroTik's trunk to the aggregation switch carries only the transfer VLAN; gateways (e.g. on Proxmox) get
   the transfer VLAN plus their setup VLAN; system switch trunks only the VLANs of their setups.
