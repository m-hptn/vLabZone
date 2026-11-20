# Nodes common to all labs

These nodes are configured the same way in `vlabzone-vrf` and `vlabzone-pbr`. Where a node behaves
differently depending on the lab, this is pointed out and the lab document has the details.
Tags **[real]** / **[sim]**: see [README.md](README.md#conventions).

The commands below work in every lab. Set the lab first:

```bash
L=vrf      # or: L=pbr
```

---

## 1. Corporate side

### 1.1 `corp-fw` – corporate network and firewall

**Role:** Stands in for the corporate network. It routes between workspaces, corporate services and the lab,
and it enforces the one-time allowance that the concept asks the corporate network team for.

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
| Bridge `br-ws` over eth1–eth3 **[sim]** | workspaces share one L2 network like VMs on a port group. containerlab only creates point-to-point links, so the bridge stands in for the switch | each workspace would need its own subnet |
| `net.ipv4.ip_forward=1` **[real]** | corp-fw routes between the segments | no traffic between corporate and lab at all |
| No route to 192.168.1.0/24, 10.198/24, 10.199/24 or 10.250/16 **[real]** | the corporate side must not know any lab-internal network. Only the edge's and the broker's outside addresses are reachable | workspaces could reach lab nets without a tunnel if the firewall had a gap |
| `bridge-nf-call-iptables=0` (if the sysctl exists) **[sim]** | if the host has `br_netfilter` loaded, bridged workspace↔workspace traffic would run through the forward chain and be dropped | workspaces could not reach each other |

**Firewall rules** (`config/corp-fw.nft`, chain `forward`, policy **drop**)

| Rule | Why |
|---|---|
| `ct state established,related accept` | replies to allowed connections. Nothing in the lab can open a connection towards corporate |
| `ct state invalid drop` | packets that belong to no known flow |
| `br-ws → eth4 accept` and back | normal corporate traffic (workspace ↔ corporate service) |
| `br-ws → eth10 udp dport $WG_PORTS accept` | **allowance 1: WireGuard.** `corp-fw.sh` defines `WG_PORTS` from the node's `EDGE_MODE`: VRF lab 51800-52099 (one port per setup, 300 setups), PBR and hybrid lab **only 51820** (E01 checks it) |
| `br-ws → eth11 tcp dport {443, 8443} accept` | **allowance 2: broker API** |
| `counter` before the drop policy | shows how much was blocked: `nft list chain inet corp forward` |

Chain `input` (policy drop) only lets workspaces ping their gateway. The firewall itself isn't reachable from the lab.

> Both labs use the same `corp-fw.nft` so they can be compared directly. For a PBR-only installation, the
> allowance shrinks to **UDP 51820**, see [vrf-vs-pbr.md](vrf-vs-pbr.md).

**Tested by:** E01 (allowance is enough), E05 (no bypass).

```bash
docker exec clab-vlabzone-$L-corp-fw nft list ruleset
docker exec clab-vlabzone-$L-corp-fw ip route
make pcap EDGE_MODE=$L NODE=corp-fw IF=eth10 SECONDS=20
```

### 1.2 `ws-std`, `ws-power`, `ws-ci` – workspaces

**Role:** Stand in for the vSphere workspace VMs, one per access mode from the concept: desktop, power user
(namespaces) and CI (alias mode, later in stage 2).

**Configuration** (`images/workspace/entrypoint.sh`)

| Setting | Why |
|---|---|
| Address 10.20.10.11 / .12 / .13, default route via corp-fw **[real]** | a normal corporate client. It knows nothing about the lab networks |
| `WS_MODE` (desktop / power / alias) **[real]** | which access mode the `lab-agent` will use (stage 2/3). Stored in `/run/vlab/ws-mode` |
| `sshpass` installed **[sim]** | non-interactive SSH logins to the devices in tests |

**Tunnel setup:** done by `tests/lib/wg.py` for now, by the `lab-agent` from stage 3. It does the same steps
the agent will, and they are **identical in all labs**. The workspace asks the edge for the key and port
(`edge-ctl pubkey` / `port`), so it doesn't need to know the edge mode.

| Step | Why |
|---|---|
| Generate the key **inside** the workspace; only the public key leaves | the private key never crosses the network (concept: keys local to the agent) |
| Register it with `edge-ctl peer add` (later: `POST /api/contexts` at the broker) | access only through the broker |
| Interface `wg-s<setup>`, endpoint `10.20.99.2:<port from edge-ctl>` | VRF lab: 51800 + setup. PBR and hybrid lab: 51820 for every setup |
| `allowed-ips 0.0.0.0/0` on the workspace side | the routes decide what enters the tunnel; the edge enforces what is allowed |
| `persistent-keepalive 25` | keeps the corporate firewall's UDP state alive so the lab can still reply after idle periods (E16) |
| Address `10.250.<user>.<setup>/32`, route `192.168.1.0/24 dev wg-s12 src 10.250.1.12` | desktop mode: the setup network is reached at its original addresses |
| **Power mode:** create the interface in the main namespace, then move it into namespace `setupN` | the encrypted socket stays outside (it needs corp-fw), and the decrypted interface exists only inside the namespace. Inside, there is no route to the corporate network at all, so several setups with the same network can be used side by side (E09, E11) |

The tunnel address encodes user and setup (`10.250.1.12` = Alice, user 1, in setup 12). In the VRF lab it
makes contexts readable in logs. In the PBR lab it is also what the edge routes on.

```bash
docker exec clab-vlabzone-$L-ws-std wg show
docker exec clab-vlabzone-$L-ws-std ip route
docker exec clab-vlabzone-$L-ws-power ip netns list
```

### 1.3 `corp-svc` – corporate service

Same image as the devices (`hwpool/device`, `SETUP=corp`, 10.20.20.10/24, real gateway 10.20.20.1).
It is the corporate target that must **not** be reachable from setups or namespaces (E05, E09), and it
shows that normal corporate traffic still works.

```bash
docker exec clab-vlabzone-$L-ws-std curl -s http://10.20.20.10      # identity of corp-svc, no tunnel needed
```

---

## 2. Lab side

### 2.1 `broker`

**Role:** Will hold bookings and grant access through `edge-ctl` and `sw-port` (stage 3). For now it only
has its final place in the network and a health endpoint. It is told the edge mode (`EDGE_MODE` env) so
it can use the right driver later.

| Setting | Why |
|---|---|
| eth1 10.20.98.2/30, route 10.20.0.0/16 via corp-fw **[real]** | reachable from workspaces only through allowance 2 (TCP 443/8443) |
| eth2 10.198.0.1/24 **[real]** | the only node on both sides: it manages edge and switch over the management network |
| `GET /healthz` on 8443 **[sim, for now]** | E01 checks that the API port is reachable through corp-fw. TLS and the API follow in stage 3 |

```bash
docker exec clab-vlabzone-$L-ws-std curl -s http://10.20.98.2:8443/healthz
```

### 2.2 `lab-agg`, `lab-sys1`, `lab-sys2` – switches

**Role:** Model the real two-tier switching (`device_arc.drawio`): a Lancom **aggregation switch** with the
edge (MikroTik) and the Proxmox nodes, and **system switches** with the devices. They stand in for the real
Lancom switches; `sw-port` is what the broker's switch driver will do on them later.

**Inventory and grouping** (`config/setups.yaml`): the switches, their ports and the devices (with IPs) are
the fixed **inventory**. Which devices form which setup is the **grouping**: a setup is a group of devices on
one system switch, several setups can share a switch, and devices can be regrouped at any time. Devices on one
switch may have the same IP; they must not end up in the same setup. Devices that are in no setup sit on
an access port without any VLAN.

| Switch | Ports |
|---|---|
| `lab-agg` | eth1: trunk to the edge · eth11 / eth12: trunks to sys1 / sys2 · eth21–23 (PBR lab): trunks to gw-12 / gw-7 / gw-5 · eth2: management 10.198.0.3 |
| `lab-sys1` | eth1: uplink trunk · eth11–14: sys1-dev1–4 (setup 12: dev1, dev2 · setup 7: dev3, dev4) · eth2: management 10.198.0.4 |
| `lab-sys2` | eth1: uplink trunk · eth11–14: sys2-dev1–4 (setup 5: dev1, dev2 · dev3, dev4 ungrouped) · eth2: management 10.198.0.5 |

Which VLANs each trunk carries depends on the lab: [lab-vrf.md](lab-vrf.md#4-switches-in-this-lab),
[lab-pbr.md](lab-pbr.md#5-switches-in-this-lab).

**Configuration** (router image, `ROLE=switch`, `SWITCH=agg|sys1|sys2`, `images/router/roles/switch.sh`,
`images/router/sw-port`). Every port's role comes from `vlab-cfg switch-config <switch>`:

```text
$ vlab-cfg switch-config sys1          (inside any node; EDGE_MODE decides the edge-facing trunks)
mgmt eth2 10.198.0.4/24
trunk eth1 112 107
access eth11 112 sys1-dev1
access eth12 112 sys1-dev2
access eth13 107 sys1-dev3
access eth14 107 sys1-dev4
```

| Setting | Why | Without it |
|---|---|---|
| Bridge `br0` with `vlan_filtering 1` **[real]** | the bridge forwards by VLAN. Without filtering, a Linux bridge is one big L2 segment | setups 12 and 7 on sys1 would share one broadcast domain: same IPs, ARP chaos, no isolation |
| `vlan_default_pvid 0`, VLAN 1 removed from bridge and trunks **[real]** | new ports land in **no** VLAN instead of the default VLAN 1. A forgotten port is cut off | a new or forgotten port would end up in VLAN 1 together with every other unconfigured port |
| Each trunk carries only the VLANs needed behind it **[real]** | aggregation → system switch: only the setups on that switch. Edge trunk: what the edge mode needs. A VLAN only exists where its setup is | a setup VLAN on a trunk where it isn't needed is one more place where a mistake can connect it to something else |
| `sw-port <port> <vlan\|none>` **[real]** | removes **all** VLANs from a port and sets exactly one untagged PVID, or none at all (ungrouped). A port is always in at most one setup. This is the whole regrouping operation (E20) | ports could carry several VLANs and connect setups |
| Ungrouped devices: access port in no VLAN **[real]** | a device that belongs to no setup is unreachable for everyone (E05) | it would be reachable from whatever VLAN its port happened to be in |
| Management IP on eth2, **outside** the bridge **[real]** | managed from 10.198.0.0/24 only; no address in any setup | a switch IP inside a setup would make it reachable from that setup |
| Ports and VLANs from `setups.yaml` via `vlab-cfg` **[sim]** | one source of truth; the test suite imports the same code (`tests/lib/labcfg.py`) | – |

**Tested by:** E02 (two setups on one switch with the same IP; across switches; trunk pruning), E05 (no
broadcast between setups on one switch, ungrouped devices unreachable), E20 (regrouping).

```bash
docker exec clab-vlabzone-$L-lab-agg bridge vlan show
docker exec clab-vlabzone-$L-lab-sys1 bridge vlan show
docker exec clab-vlabzone-$L-lab-sys1 vlab-cfg switch-config sys1
docker exec clab-vlabzone-$L-lab-sys1 bridge fdb show br br0 dynamic   # learned MACs per port and VLAN
docker exec clab-vlabzone-$L-lab-sys1 sw-port eth12 107     # move sys1-dev2 into setup 7 (undo: … eth12 112)
```

### 2.3 `sys1-dev1` … `sys2-dev4` – devices

**Role:** Stand in for real hardware on the system switches. A device is named after where it sits
(`sys1-dev1`), **not** after a setup: which setup it is in depends only on the VLAN of its switch port, and
that can change. The devices are identical in all labs and cannot tell which edge mode is in use: in all
they see `192.168.1.254` as the client.

| Device | IP | Initial setup |
|---|---|---|
| sys1-dev1 / sys1-dev2 | 192.168.1.20 / .21 | 12 |
| sys1-dev3 / sys1-dev4 | 192.168.1.20 / .22 | 7 (same switch, sys1-dev3 has the same IP as sys1-dev1) |
| sys2-dev1 / sys2-dev2 | 192.168.1.20 / .21 | 5 |
| sys2-dev3 / sys2-dev4 | 192.168.1.22 / .20 | – (ungrouped) |

**Configuration** (`images/device/entrypoint.sh`, env `NAME`, `SWITCH`, `IP`, `GW` from the topology)

| Setting | Why |
|---|---|
| Same IPs on one switch and across setups **[real]** | identical boards with the same factory address; overlapping setup networks with original IPs |
| **Default gateway 192.168.1.1, which does not exist** **[sim, on purpose]** | real devices often have an arbitrary or wrong gateway. This shows that access works anyway thanks to MASQUERADE, and fails without it (E04) |
| HTTP identity on port 80: `{"switch", "device", "ip", "peer"}` **[sim]** | `device` shows which hardware answered; tests look up which setup that device belongs to. `peer` shows the source the device sees |
| SSH (user `lab`/`lab`), banner `Device sys1-dev1 on switch sys1 - 192.168.1.20`, hostname `sys1-dev1` **[sim]** | E03: original IPs work for interactive access, and users can see which device they are on |
| rsyslog → `/var/log/auth.log` **[sim]** | E03: shows that devices log 192.168.1.254 as the source, not the user |
| `/etc/device.conf` with the own IP **[sim]** | E03: configuration that contains IPs stays valid, because nothing is renumbered |
| `iperf3 -s` **[sim]** | throughput tests (E11, E22) |
| Ready only once ports 80 and 22 listen **[sim]** | tests start right after `make up`. Without this, the first request after a deploy sometimes got "connection refused" |

```bash
docker exec clab-vlabzone-$L-sys1-dev1 tail /var/log/auth.log
docker exec clab-vlabzone-$L-sys1-dev1 ip route
```

### 2.4 `lab-mgmt`

**Role:** The L2 segment 10.198.0.0/24 that connects broker (.1), edge (.2), lab-agg (.3), lab-sys1 (.4) and
lab-sys2 (.5).

| Setting | Why |
|---|---|
| Plain bridge over eth1–eth5, no IP **[sim]** | containerlab only has point-to-point links, so this stands in for a management switch/VLAN. It has no address, so it can't be reached |
| Separate from the trunks and from the corporate side **[real]** | management traffic never shares a segment with setups or workspaces; E05 checks that neither side reaches it |

---

## 3. Settings that apply to all nodes

| Setting | Where | Why |
|---|---|---|
| Topologies generated from `setups.yaml` **[sim]** | `topology/gen.py` | the labs stay identical except for the edge mode; `make topology` regenerates them, and a test fails if a committed file is out of date |
| `network-mode: none` **[sim]** | topology defaults | containerlab would otherwise connect every node to a Docker management network, a hidden path past corp-fw. Every node only has its lab links (E05 checks there's no `eth0`) |
| Management network `vlab-mgmt`, subnet `VLAB_MGMT_SUBNET` **[sim]** | topology `mgmt:` | containerlab always creates one, even though no node uses it. Its subnet must not collide with other Docker networks on the host. Both labs share it |
| `config/` mounted read-only at `/etc/vlab` **[sim]** | topology defaults | all nodes read `setups.yaml` and `corp-fw.nft` from one place; `vlab-cfg` queries it from shell scripts |
| `EDGE_MODE` env on edge, switch and broker **[sim]** | topology | the same images serve all labs; the env selects the behaviour (corp-fw: the WireGuard allowance) |
| Wait for interfaces (`wait_if`) **[sim]** | `common.sh` | containerlab creates the links after the containers have started |
| Readiness file `/run/vlab-ready` **[sim]** | `common.sh`, `scripts/wait-ready.sh` | `make up` returns only when every node is configured |
| Privileged containers **[sim]** | containerlab default for `kind: linux` | needed for VRFs, WireGuard, bridges, sysctls. This is why the lab belongs on a dedicated host |

---

## 4. Carried over to the real environment (all modes)

1. Corporate allowance: WireGuard UDP to the edge (VRF: 51800–52099, PBR and hybrid: 51820), TCP 443/8443 to the broker.
   Nothing else, and no routes to lab-internal networks.
2. Switches: VLAN filtering, no default VLAN on ports, at most one untagged VLAN per access port (none for
   ungrouped devices), every trunk with only the VLANs needed behind it, management outside the setups.
8. Regrouping devices = changing access-port VLANs on the system switch; afterwards the setup gateways must
   forget stale ARP entries. Two devices with the same IP must never be grouped into one setup (the broker refuses).
3. Peers only through the broker, `/32` allowed IPs per key, conntrack flush on revoke.
4. Edge keys must be persistent in production (in the lab they are regenerated on every start).
5. The edge firewall must restrict what tunnels may reach **on the edge itself** by destination address
   (only the tunnel gateway).
6. Changing a WireGuard interface interrupts all its sessions for about 15 s: plan changes to the edge accordingly.
7. Devices only see the setup gateway address as the client; user attribution must come from the broker's audit log.

Mode-specific items: [lab-vrf.md](lab-vrf.md#carried-over-to-the-real-environment) and
[lab-pbr.md](lab-pbr.md#carried-over-to-the-real-environment).
