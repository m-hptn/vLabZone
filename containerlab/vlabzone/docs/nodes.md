# Lab nodes – configuration and rationale

This document explains **what each node of the running labs is configured to do and why**.

There are two labs, generated from the same source by `topology/gen.py`:

| Lab | Containers | Edge | Extra nodes |
|---|---|---|---|
| `vlabzone-vrf` | `clab-vlabzone-vrf-<node>` | VRF per setup (sections 3.1–3.7) | – |
| `vlabzone-pbr` | `clab-vlabzone-pbr-<node>` | one `wg0` + policy routing (section 3.8) | setup gateways `gw-N` (section 3.9) |

All other nodes are identical in both labs. Command examples use the VRF lab; replace `vrf` with `pbr`
in the container name for the other one.

- Concept and experiments: [`../../../idea.md`](../../../idea.md) (German)
- Source of truth for setups, VLANs and devices: [`../config/setups.yaml`](../config/setups.yaml)
- Topologies (generated, do not edit): [`vlabzone-vrf.clab.yml`](../topology/vlabzone-vrf.clab.yml),
  [`vlabzone-pbr.clab.yml`](../topology/vlabzone-pbr.clab.yml), generator [`gen.py`](../topology/gen.py)

Every configuration item is tagged:

- **[real]**: belongs to the architecture and carries over to the real environment (Proxmox/MikroTik/switches).
- **[sim]**: only exists because the environment is simulated in containers.

All command outputs below come from the running lab.

---

## 1. Overview

```text
                  corporate (simulated)                     │                 lab zone
                                                           │
 ws-std   10.20.10.11 ─┐                                    │
 ws-power 10.20.10.12 ─┼─ br-ws ┐                           │                         ┌ eth11 dev-12-20 192.168.1.20 ┐ VLAN 112
 ws-ci    10.20.10.13 ─┘        │                           │                         ├ eth12 dev-12-21 192.168.1.21 ┘ setup 12
                             corp-fw ── eth10 ─ UDP 51800-52099 ─ eth1 lab-edge eth2 ═ trunk ═ eth1 lab-sw
 corp-svc 10.20.20.10 ── eth4 ─┘  └── eth11 ─ TCP 443/8443 ─ eth1 broker  │ eth3     112,107,199  ├ eth21 dev-7-20  192.168.1.20 ┐ VLAN 107
                                                           │      │ eth2   │                      └ eth22 dev-7-21  192.168.1.21 ┘ setup 7
                                                           │      └──── lab-mgmt (10.198.0.0/24) ── eth2 lab-sw
```

| Node | Image | Role | Concept counterpart |
|---|---|---|---|
| `ws-std`, `ws-power`, `ws-ci` | `hwpool/workspace` | workspace VMs | vSphere workspaces |
| `corp-svc` | `hwpool/device` | corporate service | e.g. file server in the corporate network |
| `corp-fw` | `hwpool/router` (`ROLE=corp-fw`) | corporate routing and firewall | corporate network with the one-time allowance |
| `lab-edge` | `hwpool/edge` | WireGuard, VRF per setup, NAT | lab edge (later MikroTik) |
| `broker` | `hwpool/broker` | booking/peer API (stage 3) | broker |
| `lab-sw` | `hwpool/router` (`ROLE=switch`) | VLAN switch | lab switches |
| `lab-mgmt` | `hwpool/mgmt` | L2 segment for management | lab management network |
| `dev-N-x` | `hwpool/device` | devices under test | real hardware in the setups |

### Addressing

| Segment | Network | Why this way |
|---|---|---|
| Workspaces | 10.20.10.0/24 | one corporate client net |
| Corporate services | 10.20.20.0/24 | a second corporate net, so isolation from it can be tested |
| corp-fw ↔ lab-edge | 10.20.99.0/30 | the **only** path for tunnel traffic |
| corp-fw ↔ broker | 10.20.98.0/30 | the **only** path for API traffic |
| Lab management | 10.198.0.0/24 | broker controls edge and switch, invisible to the corporate side |
| WireGuard tunnels | 10.250.0.0/16 | edge 10.250.0.1; context = 10.250.\<user\>.\<setup\> |
| Setups 12 and 7 | 192.168.1.0/24 (both) | deliberately the **same** network: overlapping setups are the main requirement |

The tunnel address encodes user and setup (`10.250.1.12` = Alice in setup 12). The edge can then
tell contexts apart even though all setups use identical addresses, and the address is easy to read in logs.

---

## 2. One request, hop by hop

Alice in `ws-std` has a tunnel to setup 12 and runs `curl http://192.168.1.20`.
Each line was captured at the named hop (TCP SYN, shortened):

| # | Hop | What is seen | What happened |
|---|---|---|---|
| 1 | `ws-std` wg-s12 | `10.250.1.12.41834 > 192.168.1.20.80 [S] mss 1380` | the route `192.168.1.0/24 dev wg-s12 src 10.250.1.12` sends it into the tunnel |
| 2 | `ws-std` eth1 | `10.20.10.11.54059 > 10.20.99.2.51812: UDP, length 96` | WireGuard encrypts it; only UDP leaves the workspace |
| 3 | `corp-fw` eth10 | `10.20.10.11.54059 > 10.20.99.2.51812: UDP` | the corporate side sees only encrypted UDP to the allowed port range |
| 4 | `lab-edge` wg-s12 | `10.250.1.12.41834 > 192.168.1.20.80 [S]` | decrypted. The key allows only source `10.250.1.12/32`, so it is accepted. It now lives in VRF `vrf-s12` |
| 5 | `lab-edge` eth2 | `vlan 112 … 192.168.1.254.41834 > 192.168.1.20.80` | routed in table 1012 to `eth2.112`, **MASQUERADE** to 192.168.1.254, tagged with VLAN 112 |
| 6 | `lab-sw` eth11 | `192.168.1.254.41834 > 192.168.1.20.80` (no tag) | the switch only forwards VLAN 112 to setup 12's access ports and removes the tag |
| 7 | `dev-12-20` eth1 | `192.168.1.254.41834 > 192.168.1.20.80` | the device sees a client on its own subnet and answers it directly |

The edge's conntrack entry shows the translation in both directions:

```text
src=10.250.1.12 dst=192.168.1.20 sport=41820 dport=80   src=192.168.1.20 dst=192.168.1.254 sport=80 dport=41820
```

The reply takes the same path backwards. The edge reverses the NAT and routes `10.250.1.12` in table 1012 into `wg-s12`.
WireGuard picks the peer whose allowed IPs contain `10.250.1.12`, encrypts the reply and sends it back to the
workspace's UDP endpoint.

`mss 1380` comes from the WireGuard MTU of 1420 (1500 − 80 bytes overhead). The containerlab links themselves
use MTU 9500, so the lab will not show MTU problems by itself. Experiment E15 lowers the MTU on purpose.

---

## 3. Nodes

### 3.1 `corp-fw` – corporate network and firewall

**Role:** Stands in for the corporate network: it routes between workspaces, corporate services and the lab, and
it enforces the one-time allowance that the concept asks the corporate network team for.

**Interfaces**

| Interface | Address | Connected to |
|---|---|---|
| `br-ws` (eth1–eth3) | 10.20.10.1/24 | ws-std, ws-power, ws-ci |
| eth4 | 10.20.20.1/24 | corp-svc |
| eth10 | 10.20.99.1/30 | lab-edge |
| eth11 | 10.20.98.1/30 | broker |

**Configuration** (`images/router/roles/corp-fw.sh`, `config/corp-fw.nft`)

| Setting | Why | Without it |
|---|---|---|
| Bridge `br-ws` over eth1–eth3 **[sim]** | Workspaces share one L2 network like VMs on a port group. containerlab only creates point-to-point links, so the bridge stands in for the switch. | each workspace would need its own subnet |
| `net.ipv4.ip_forward=1` **[real]** | corp-fw routes between the segments | no traffic between corporate and lab at all |
| No route to 192.168.1.0/24, 10.198/24 or 10.250/16 **[real]** | the corporate side must not know any lab-internal network. Only the edge's outside address is reachable | workspaces could reach lab nets without a tunnel if the firewall had a gap |
| `bridge-nf-call-iptables=0` (if the sysctl exists) **[sim]** | if the host has `br_netfilter` loaded, bridged workspace↔workspace traffic would run through the forward chain and be dropped | workspaces could not reach each other |

**Firewall rules** (`config/corp-fw.nft`, chain `forward`, policy **drop**)

| Rule | Why |
|---|---|
| `ct state established,related accept` | replies to allowed connections. Nothing in the lab can open a connection towards corporate |
| `ct state invalid drop` | packets that belong to no known flow |
| `br-ws → eth4 accept` and back | normal corporate traffic (workspace ↔ corporate service) |
| `br-ws → eth10 udp dport 51800-52099 accept` | **allowance 1:** WireGuard. One port per setup in VRF mode (51800 + setup), so the range covers 300 setups |
| `br-ws → eth11 tcp dport {443, 8443} accept` | **allowance 2:** broker API |
| `counter` before the drop policy | shows how much was blocked: `nft list chain inet corp forward` |

Chain `input` (policy drop) only lets workspaces ping their gateway. The firewall itself isn't reachable from the lab.

**Tested by:** E01 (allowance is enough), E05 (no bypass).

**Inspect**

```bash
docker exec clab-vlabzone-vrf-corp-fw nft list ruleset
docker exec clab-vlabzone-vrf-corp-fw ip route
make pcap NODE=corp-fw IF=eth10 SECONDS=20
```

---

### 3.2 `lab-edge` – WireGuard, VRF per setup, NAT

**Role:** The only entry into the lab zone. It ends the tunnels, puts each tunnel into the VRF of exactly
one setup and makes the setup's devices reachable under their original IPs. This is the node that will
later be the MikroTik.

**Interfaces**

| Interface | Address | Purpose |
|---|---|---|
| eth1 | 10.20.99.2/30 | corporate side, receives only WireGuard UDP |
| eth2 | – | trunk to lab-sw (tagged VLANs) |
| eth2.112 (in vrf-s12) | 192.168.1.254/24 | gateway in setup 12 |
| eth2.107 (in vrf-s7) | 192.168.1.254/24 | gateway in setup 7, **same address** as in setup 12 |
| wg-s12 (in vrf-s12) | 10.250.0.1/16, UDP 51812 | tunnels to setup 12 |
| wg-s7 (in vrf-s7) | 10.250.0.1/16, UDP 51807 | tunnels to setup 7 |
| eth3 | 10.198.0.2/24 | management (broker, stage 3) |

**Configuration** (`images/edge/entrypoint.sh`, `images/edge/edge-vrf.sh`)

| Setting | Why | Without it |
|---|---|---|
| One VRF per setup (`vrf-sN`, table 1000+N) **[real]** | Each VRF has its own routing table, so `192.168.1.0/24` can exist once per setup. Traffic from `wg-s12` is only ever looked up in table 1012 | overlapping setups would be impossible: one routing table can hold 192.168.1.0/24 only once |
| VLAN subinterface `eth2.<vlan>` in the VRF **[real]** | binds the setup's L2 segment to its VRF | no path from the VRF into the setup |
| Same gateway IP `192.168.1.254` in every VRF **[real]** | one uniform address per setup. It is allowed because each VRF is separate | – |
| One WireGuard interface per setup, port 51800+N **[real]** | the port selects the setup. One interface can belong to one VRF only, so each setup needs its own | a single interface would need policy routing (that is the PBR variant, stage 2) |
| WireGuard interface in the VRF, its UDP socket outside **[real]** | the encrypted UDP traffic arrives on eth1 (default VRF), and the decrypted packets appear in `vrf-sN` | if the socket were in the VRF, it would never receive anything from eth1 |
| `net.ipv4.udp_l3mdev_accept` / `tcp_l3mdev_accept` left at **0** **[real]** | tested: tunnels work without it, because the WireGuard sockets only receive on eth1, which is outside any VRF. Setting it to 1 would let sockets on the edge itself accept traffic arriving from inside a setup | with 1: services on the edge (e.g. SSH for the broker) would be reachable from the setups, leaving only the firewall in between |
| `ip_forward=1` **[real]** | the edge routes between tunnel and VLAN | nothing gets through |
| Only `10.20.0.0/16 via 10.20.99.1` in the main table, **no default route** **[real]** | the edge only needs to reach the corporate clients | a default route could become a path from the lab to the internet or into other networks |
| Private keys generated at start, in `/run/vlab/keys` **[sim → real]** | every start gets new keys, which is what E18 (edge restart) needs. In the real setup the keys must be persistent and backed up | – |
| **No peers at start** **[real]** | access exists only while the broker grants it. A restart drops all access until the broker restores it (E18) | stale access after a restart |

**Firewall and NAT** (nft table `inet edge`)

| Chain / rule | Why |
|---|---|
| `input` policy drop: `iifname eth1 udp dport 51800-52099 accept` | only WireGuard from the corporate side reaches the edge itself |
| `input`: `meta sdifname "wg-s*" icmp echo-request accept` | ping to the tunnel gateway 10.250.0.1 for diagnosis |
| `input`: `iifname eth3 tcp dport 22 accept` | management access for the broker (used from stage 3) |
| `forward` policy drop, `ct state established,related accept` | replies only; a setup cannot open connections anywhere (E05) |
| `setups`: `meta sdifname "wg-s12" oifname "eth2.112" accept` | the only allowed new flow: tunnel of setup N → VLAN of setup N |
| `nat-sN`: `oifname "eth2.<vlan>" masquerade` | devices see `192.168.1.254` as the client and answer on their own subnet. That needs no gateway on the device, and no route back to 10.250/16. A separate chain per setup lets `edge-ctl nat off` switch it off for E04 |

> **Important finding (VRF + nftables) [real]:** for traffic that arrives on an interface inside a VRF,
> the `input` and `forward` hooks see the **VRF device** (`vrf-s12`) as `iifname`, not `wg-s12`.
> Rules have to match the real ingress interface with **`meta sdifname`**. With `iifname "wg-s12"` all
> tunnel traffic was silently dropped. The real edge configuration (MikroTik, or Linux) must account for this.

**`edge-ctl`**: the single interface for granting and revoking access. The test helpers use it now, and the broker will call it over SSH from stage 3.

| Command | Effect |
|---|---|
| `edge-ctl peer add --setup N --pubkey K --ip 10.250.U.N` | `wg set wg-sN peer K allowed-ips 10.250.U.N/32`. **Cryptokey routing:** this key may only send from this one address (E06) |
| `edge-ctl peer del --setup N --pubkey K` | removes the peer and deletes its conntrack entries, so running sessions end at once (E08) |
| `edge-ctl peer list`, `pubkey`, `port`, `status` | state for tests and the later reconcile loop |
| `edge-ctl nat on\|off --setup N` | switch MASQUERADE (E04) |

It accepts only well-formed keys, known setups and addresses in 10.250.0.0/16.

**Tested by:** E01–E06, E09.

**Inspect**

```bash
docker exec clab-vlabzone-vrf-lab-edge edge-ctl status
docker exec clab-vlabzone-vrf-lab-edge ip -br addr
docker exec clab-vlabzone-vrf-lab-edge ip route show vrf vrf-s12
docker exec clab-vlabzone-vrf-lab-edge wg show
docker exec clab-vlabzone-vrf-lab-edge nft list ruleset
docker exec clab-vlabzone-vrf-lab-edge conntrack -L
```

---

### 3.3 `lab-sw` – VLAN switch

**Role:** Puts each device into its setup's VLAN and connects the edge through one trunk.
It stands in for the real lab switches, where `sw-port` will become the Ansible automation.

**Interfaces**

| Interface | VLANs | Connected to |
|---|---|---|
| eth1 (trunk) | VRF lab: 107, 112 tagged · PBR lab: 199 only | lab-edge eth2 |
| eth11, eth12 | 112 untagged (PVID) | dev-12-20, dev-12-21 |
| eth21, eth22 | 107 untagged (PVID) | dev-7-20, dev-7-21 |
| eth2 | 10.198.0.3/24, not bridged | lab-mgmt |
| PBR lab only: eth18 / eth19, eth28 / eth29 | 112 / 199, 107 / 199 untagged | gw-12 setup / transfer port, gw-7 setup / transfer port |

**Configuration** (`images/router/roles/switch.sh`, `images/router/sw-port`)

| Setting | Why | Without it |
|---|---|---|
| Bridge `br0` with `vlan_filtering 1` **[real]** | the bridge forwards by VLAN. Without filtering, a Linux bridge is one big L2 segment | setup 12 and setup 7 would share one broadcast domain: same IPs, ARP chaos, no isolation |
| `vlan_default_pvid 0` and VLAN 1 removed from the bridge and trunk **[real]** | new ports land in **no** VLAN instead of the default VLAN 1. A forgotten port is cut off instead of joining a shared default VLAN | a new or forgotten port would end up in VLAN 1 together with every other unconfigured port |
| Trunk carries only the VLANs the edge needs (`vlab-cfg trunk-vlans`) **[real]** | VRF: the edge is the gateway in every setup, so it gets every setup VLAN. PBR: the edge only talks to the gateways, so it gets VLAN 199 and **no** setup VLAN | an edge with setup VLANs it doesn't use is one more way into the setups |
| `sw-port <port> <vlan>` **[real]** | removes **all** VLANs from a port and sets exactly one untagged PVID. A port is therefore always in exactly one setup (E20 moves devices this way) | ports could carry several VLANs and connect setups |
| Management IP on eth2, **outside** the bridge **[real]** | the switch is managed from 10.198.0.0/24 only and has no address in any setup | a switch IP inside a setup would make it reachable, and attackable, from that setup |
| Ports from `setups.yaml`, missing ports skipped **[sim]** | one source of truth for ports and VLANs | – |

**Tested by:** E02 (no packets in the wrong setup), E05 (no broadcast between setups).

**Inspect**

```bash
docker exec clab-vlabzone-vrf-lab-sw bridge vlan show
docker exec clab-vlabzone-vrf-lab-sw bridge fdb show br br0 dynamic   # learned MACs per port and VLAN
docker exec clab-vlabzone-vrf-lab-sw sw-port eth12 107     # move dev-12-21 into setup 7 (undo: … eth12 112)
```

---

### 3.4 `dev-12-20`, `dev-12-21`, `dev-7-20`, `dev-7-21` – devices

**Role:** Stand in for real hardware. Every device has a unique identity so a test can prove where a
packet arrived, even though both setups use the same addresses.

**Configuration** (`images/device/entrypoint.sh`, env from the topology)

| Setting | Why |
|---|---|
| IP `192.168.1.20/.21` in **both** setups **[real]** | the core requirement: overlapping setup networks with original IPs |
| **Default gateway 192.168.1.1, which does not exist** **[sim, on purpose]** | real devices often have an arbitrary or wrong gateway. This shows that access works anyway thanks to MASQUERADE, and fails without it (E04) |
| HTTP identity on port 80: `{"setup", "device", "ip", "peer"}` **[sim]** | `setup`/`device` show where the request landed; `peer` shows the source the device sees (192.168.1.254) |
| SSH (user `lab`/`lab`), banner `Setup 12 - device dev-12-20 - 192.168.1.20`, hostname `setup12-20` **[sim]** | E03: original IPs work for interactive access, and the user can see which setup they are in |
| rsyslog → `/var/log/auth.log` **[sim]** | E03: documents that devices log 192.168.1.254 as source, not the user |
| `/etc/device.conf` with the own IP **[sim]** | E03: configuration that contains IPs stays valid, because nothing is renumbered |
| `iperf3 -s` **[sim]** | throughput tests (E11, E22) |
| Ready only once ports 80 and 22 listen **[sim]** | tests start right after `make up`. Without this, the first request after a deploy sometimes got "connection refused" |

`corp-svc` uses the same image (`SETUP=corp`, 10.20.20.10, real gateway 10.20.20.1).
It is the corporate target that must **not** be reachable from setups or namespaces (E05, E09).

**Inspect**

```bash
docker exec clab-vlabzone-vrf-ws-std curl -s http://10.20.20.10      # corp-svc identity, without tunnel
docker exec clab-vlabzone-vrf-dev-12-20 tail /var/log/auth.log
docker exec clab-vlabzone-vrf-dev-12-20 ip route
```

---

### 3.5 `ws-std`, `ws-power`, `ws-ci` – workspaces

**Role:** Stand in for the vSphere workspace VMs, one per access mode from the concept: desktop, power user
(namespaces) and CI (alias mode, stage 2).

**Configuration** (`images/workspace/entrypoint.sh`)

| Setting | Why |
|---|---|
| Address 10.20.10.1x, default route via corp-fw **[real]** | a normal corporate client. It knows nothing about the lab networks |
| `WS_MODE` (desktop / power / alias) **[real]** | which access mode the `lab-agent` will use (stage 2/3). Stored in `/run/vlab/ws-mode` |
| `sshpass` installed **[sim]** | non-interactive SSH logins to the devices in tests |

**Tunnel setup**: `tests/lib/wg.py` now, `lab-agent` from stage 3. It does the same steps the agent will:

| Step | Why |
|---|---|
| Generate the key **inside** the workspace; only the public key leaves | the private key never crosses the network (concept: keys local to the agent) |
| Register it with `edge-ctl peer add` (later: `POST /api/contexts` at the broker) | access only through the broker |
| `wg-s<setup>` with endpoint `10.20.99.2:<51800+setup>` | the port selects the setup |
| `allowed-ips 0.0.0.0/0` on the workspace side | the routes decide what enters the tunnel; the edge enforces what is allowed |
| `persistent-keepalive 25` | keeps the corporate firewall's UDP state alive so the lab can still reply after idle periods (E16) |
| Address `10.250.<user>.<setup>/32`, route `192.168.1.0/24 dev wg-s12 src 10.250.1.12` | desktop mode: the setup network is reached at its original addresses |
| **Power mode:** create the interface in the main namespace, then move it into namespace `setupN` | the encrypted socket stays outside (it needs corp-fw), and the decrypted interface exists only inside the namespace. Inside, there is no route to the corporate network at all, so several setups with the same network can be used side by side (E09, E11) |

**Inspect**

```bash
docker exec clab-vlabzone-vrf-ws-std wg show
docker exec clab-vlabzone-vrf-ws-std ip route
docker exec clab-vlabzone-vrf-ws-power ip netns list
```

---

### 3.6 `broker`

**Role:** Will hold bookings and grant access through `edge-ctl` and `sw-port` (stage 3). In stage 1 it only
has its final place in the network and a health endpoint.

| Setting | Why |
|---|---|
| eth1 10.20.98.2/30, route 10.20.0.0/16 via corp-fw **[real]** | reachable from workspaces only through allowance 2 (TCP 443/8443) |
| eth2 10.198.0.1/24 **[real]** | the only node on both sides: it manages edge and switch over the management network |
| `GET /healthz` on 8443 **[sim, for now]** | E01 checks that the API port is reachable through corp-fw. TLS and the API follow in stage 3 |

```bash
docker exec clab-vlabzone-vrf-ws-std curl -s http://10.20.98.2:8443/healthz
```

---

### 3.7 `lab-mgmt`

**Role:** The L2 segment 10.198.0.0/24 that connects broker (.1), edge (.2) and switch (.3).

| Setting | Why |
|---|---|
| Plain bridge over eth1–eth3, no IP **[sim]** | containerlab only has point-to-point links, so this stands in for a management switch/VLAN. It has no address, so it can't be reached |
| Separate from the trunk and from the corporate side **[real]** | management traffic never shares a segment with setups or workspaces; E05 checks that neither side reaches it |

### 3.8 `lab-edge` in PBR mode (lab `vlabzone-pbr`)

**Role:** Same job as in VRF mode, done differently. A single WireGuard interface takes all tunnels,
and **policy routing** sends each tunnel address to the gateway of its setup. The edge itself has
no interface in any setup.

**Interfaces**

| Interface | Address | Purpose |
|---|---|---|
| eth1, eth3 | as in VRF mode | corporate side, management |
| wg0 | 10.250.0.1/16, UDP **51820** | all tunnels, all setups |
| eth2.199 | 10.199.0.1/24 | transfer VLAN to the gateways gw-N (10.199.0.N) |

**Configuration** (`images/edge/edge-pbr.sh`)

| Setting | Why | Without it |
|---|---|---|
| One `wg0` on port 51820 **[real]** | one port for everything; 51820 is inside the corporate allowance | – |
| Table `1000+N` per setup with only `default via 10.199.0.N` **[real]** | everything a context sends goes to the gateway of its setup and nowhere else | – |
| Per booking: `ip rule from 10.250.U.N iif wg0 lookup 1000+N pref 10000` **[real]** | **the** isolation mechanism of PBR: the tunnel source address picks the table. `/32` allowed IPs per key make sure the address can't be faked | traffic would be looked up in the main table, which has no route into any setup → no access (tested: fail-closed) |
| Main table without routes into setups **[real]** | a missing rule must not lead anywhere | a missing rule would leak into whichever setup the main table points to |
| Forward only `wg0 → eth2.199`, replies via conntrack **[real]** | the edge never forwards from the transfer VLAN into tunnels on its own | gateways or setups could open connections to workspaces |
| Ping on wg0 only to `10.250.0.1` **[real]** | the `local` routing table is consulted before any `ip rule` lookup, so **every** address of the edge (10.199.0.1, 10.198.0.2, …) is reachable from a tunnel. The firewall must restrict by destination | E10 found the edge answering pings on its transfer and management addresses from a tunnel |

`edge-ctl` accepts the same commands as in VRF mode. `peer add` makes **two** changes here (WireGuard peer
+ ip rule), `peer del` removes both. `pubkey`/`port` return wg0's values for every setup. `nat` is not
on the edge in this mode: use `gw-ctl` on gw-N.

```bash
docker exec clab-vlabzone-pbr-lab-edge edge-ctl status
docker exec clab-vlabzone-pbr-lab-edge ip rule show
docker exec clab-vlabzone-pbr-lab-edge ip route show table 1012
docker exec clab-vlabzone-pbr-lab-edge edge-ctl peer list
```

---

### 3.9 `gw-12`, `gw-7` – setup gateways (lab `vlabzone-pbr`)

**Role:** Gateway between the transfer VLAN and one setup. In PBR mode these take the part the VRF
interfaces play in VRF mode: the setup's gateway address and the MASQUERADE.

**Interfaces** (router image, `ROLE=gw`, `SETUP=N`, `images/router/roles/gw.sh`)

| Interface | Address | Switch port |
|---|---|---|
| eth1 | 10.199.0.N/24 (transfer) | VLAN 199 |
| eth2 | 192.168.1.254/24 (setup gateway) | the setup's VLAN |

| Setting | Why | Without it |
|---|---|---|
| Route `10.250.0.0/16 via 10.199.0.1`, **no default route** **[real]** | replies to the tunnels go back to the edge; nothing else is reachable from the setup | a default route would be a way out of the setup |
| Forward only `eth1 → eth2` from `10.250.0.0/16`, replies via conntrack **[real]** | only tunnel traffic enters the setup. Nothing goes from the setup into the transfer VLAN, so there is no path to the other gateways or the edge (E10 checks it) | all gateways share VLAN 199; a setup could reach the other setups' gateways |
| MASQUERADE on eth2, in its own chain **[real]** | same reason as on the VRF edge: devices with a wrong gateway answer `.254` (E04). `gw-ctl nat on\|off` switches it | no answers from devices with a wrong gateway |
| Input: only ping from the edge **[real]** | the gateway is diagnosable from the edge but not reachable from the setup or the tunnels | – |

```bash
docker exec clab-vlabzone-pbr-gw-12 gw-ctl status
docker exec clab-vlabzone-pbr-gw-12 nft list ruleset
```

---

### 3.10 VRF vs. PBR: what E10 found

Both labs pass every functional test (E01–E06, E09). The differences are in configuration effort and in
what happens when something is configured wrong:

| | VRF | PBR |
|---|---|---|
| Objects changed per booking | 1 (WireGuard peer) | 2 (WireGuard peer + ip rule) |
| UDP ports in the corporate allowance | one per setup (51800 + N) | one (51820) |
| Per-booking mistake: peer registered on the wrong setup / rule missing | **fail-closed**: key unknown on that interface, or main table has no route | **fail-closed** (rule missing) |
| Per-booking mistake: rule points to the wrong table | – (no per-booking routing) | **access to the wrong setup**, two-way: Alice booked 12 and lands in 7 |
| Setup-level mistake: tunnel interface in the wrong VRF (+ matching forward rule) | **one-way leak**: requests reach setup 7, replies are routed to wg-s7 and dropped | – |
| Edge reachable from tunnels | only 10.250.0.1 | every edge address through the `local` table, unless the firewall restricts by destination |
| Setup gateway | the edge itself | separate gateway per setup |

Consequence: in PBR, **every single booking** is a chance to put a user into a foreign setup. In VRF,
that takes a setup-level mistake, which is made once per setup and can be checked.

Also found: any change to a WireGuard interface (VRF membership, down/up) drops its sessions. Workspaces
only notice after WireGuard's retry timer, about **12–16 s** in the lab. Relevant for E18 (edge restart).

---

## 4. Settings that apply to all nodes

| Setting | Where | Why |
|---|---|---|
| Topologies generated from `setups.yaml` **[sim]** | `topology/gen.py` | both labs stay identical except for the edge mode; `make topology` regenerates, a test fails if a committed file is stale |
| `network-mode: none` **[sim]** | topology defaults | containerlab would otherwise connect every node to a Docker management network, a hidden path past corp-fw. Every node only has its lab links (E05 checks there's no `eth0`) |
| Management network `vlab-mgmt`, subnet `VLAB_MGMT_SUBNET` **[sim]** | topology `mgmt:` | containerlab always creates one even if no node uses it; its subnet must not collide with other Docker networks on the host |
| `config/` mounted read-only at `/etc/vlab` **[sim]** | topology defaults | all nodes read `setups.yaml` and `corp-fw.nft` from one place; `vlab-cfg` queries it from shell scripts |
| Wait for interfaces (`wait_if`) **[sim]** | `common.sh` | containerlab creates the links after the containers have started |
| Readiness file `/run/vlab-ready` **[sim]** | `common.sh`, `scripts/wait-ready.sh` | `make up` returns only when every node is configured |
| Privileged containers **[sim]** | containerlab default for `kind: linux` | needed for VRFs, WireGuard, bridges, sysctls. This is why the lab belongs on a dedicated host |

---

## 5. Carried over to the real environment

Items marked **[real]** above are the ones that should become part of the real lab edge and switch
configuration and of the request to the corporate network team:

1. Corporate allowance: UDP 51800–52099 to the edge, TCP 443/8443 to the broker. Nothing else, and no routes to lab-internal networks.
2. Edge: VRF + VLAN + WireGuard per setup, MASQUERADE per setup VLAN, forward only "tunnel N → VLAN N", no default route, `l3mdev_accept` off.
3. Firewall rules inside VRFs must match the real ingress interface (`meta sdifname` on Linux; to be checked for RouterOS in stage 1b).
4. Switch: VLAN filtering, no default VLAN on ports, exactly one untagged VLAN per access port, management outside the setups.
5. Peers only through the broker, `/32` allowed IPs per key, conntrack flush on revoke.
6. Edge keys must be persistent in production (in the lab they are regenerated on every start).
7. The edge firewall must restrict what tunnels may reach **on the edge itself** by destination address (only the tunnel gateway), especially with PBR.
8. Changing a WireGuard interface interrupts all its sessions for ~15 s: plan changes to the edge accordingly.
9. If PBR is chosen: the broker must verify every ip rule it creates (reconcile), because a wrong table gives access to a foreign setup.
