# Labs `vlabzone-<mode>-ros` – the MikroTik as lab edge (E27)

Start: `make up EDGE_IMPL=routeros EDGE_MODE=vrf|pbr|hybrid` · Tests: `make test EDGE_IMPL=routeros EDGE_MODE=…` ·
Topologies: [`vlabzone-<mode>-ros.clab.yml`](../topology/) (generated) · Needs `/dev/kvm` and `make build-routeros`

The same three labs as with the Linux edge ([lab-vrf.md](lab-vrf.md), [lab-pbr.md](lab-pbr.md),
[lab-hybrid.md](lab-hybrid.md)), with **one** difference: `lab-edge` is a MikroTik RouterOS CHR (7.24.6,
the version of stage 1b). Every other node, all addresses, the switches and the tests stay the same.
This answers the parity question of the plan (E27): does the real edge platform behave like the Linux
edge the designs were developed on?

**Answer: yes, in all three modes.** The functional suite passes unchanged, and the misconfiguration
tests of E10, repeated on RouterOS (E27), give the same results. Two things had to be done differently
on RouterOS, and one gap was found and closed (section 4).

---

## 1. How it works

```text
make up EDGE_IMPL=routeros EDGE_MODE=hybrid
  1. containerlab deploy topology/vlabzone-hybrid-ros.clab.yml      lab-edge = kind mikrotik_ros
  2. scripts/wait-ready.sh                                          every node except the CHR
  3. drivers/edge_routeros.py --lab vlabzone-hybrid-ros --mode hybrid configure
                                                                    REST: interfaces, VRFs, rules, NAT, firewall
```

| Part | What |
|---|---|
| [`drivers/edge_routeros.py`](../drivers/edge_routeros.py) | the RouterOS edge driver: `configure` builds the edge from `config/setups.yaml`; all other commands are those of `edge-ctl` (`peer add/del/list`, `pubkey`, `port`, `nat`, `neigh flush`, `status`) with the same arguments, output and exit codes. The broker will use it in stage 3 |
| [`topology/gen.py`](../topology/gen.py) | writes `vlabzone-<mode>-ros.clab.yml`: same lab, `lab-edge` of kind `mikrotik_ros`. `network-mode: none` moves from the defaults to every other node, because the CHR needs its management interface |
| tests | `lab-edge` is a `RouterOSEdge` node ([`tests/lib/node.py`](../tests/lib/node.py)): `edge-ctl …` goes to the driver; any other command (nft, ip rule, wg) looks at Linux internals and the test is **skipped** with that reason. E27 repeats those E10 cases on RouterOS |
| `edge-ctl neigh flush --setup N` | new on both edges: forget a setup's ARP entries after regrouping (E20). Before, the test fixture ran `ip neigh flush` on the Linux edge directly |

**Interfaces** (containerlab `ethN` = RouterOS `ether(N+1)`):

| RouterOS | Lab link | Address | |
|---|---|---|---|
| ether1 | containerlab management network | from `VLAB_MGMT_SUBNET` | REST for the driver and tests **[sim]**, see section 3 |
| ether2 | corp-fw eth10 | 10.20.99.2/30 | corporate side, WireGuard |
| ether3 | lab-agg eth1 | – (VLAN trunk) | `vlanN` per setup (VRF, hybrid) or `vlan199` (PBR) |
| ether4 | lab-mgmt | 10.198.0.2/24 | lab management (broker: SSH/REST) |

---

## 2. Configuration per mode

What `configure` creates (read back from the running labs; equivalent CLI). Common to all modes:

```routeros
/ip address add address=10.20.99.2/30 interface=ether2
/ip address add address=10.198.0.2/24 interface=ether4
/ip route add dst-address=10.20.0.0/16 gateway=10.20.99.1          # corporate nets only, no default route
# alias addresses (E13), per setup – only for the setup's own tunnel prefix
/ip firewall nat add chain=dstnat src-address=10.250.12.0/24 dst-address=10.200.12.0/24 action=netmap to-addresses=192.168.1.0/24
/system identity set name=lab-edge-<mode>                          # marks the router as configured
```

**Hybrid** (one port, setups in VRFs, static rule per setup):

```routeros
/interface wireguard add name=wg0 listen-port=51820
/ip address add address=10.250.0.1/16 interface=wg0
# per setup (12 shown)
/interface vlan add name=vlan112 vlan-id=112 interface=ether3
/ip vrf add name=vrf-s12 interfaces=vlan112
/ip address add address=192.168.1.254/24 interface=vlan112
/ip firewall nat add chain=srcnat out-interface=vlan112 action=masquerade
/ip route add dst-address=10.250.0.0/16 gateway=wg0@main routing-table=vrf-s12
/routing rule add src-address=10.250.12.0/24 interface=wg0 table=vrf-s12 action=lookup-only-in-table
/ip firewall mangle add chain=forward out-interface=vlan112 action=mark-packet new-packet-mark=to-s12 passthrough=yes
/ip firewall filter add chain=forward in-interface=wg0 packet-mark=to-s12 src-address=10.250.12.0/24 action=accept
```

**VRF** (one WireGuard interface and port per setup):

```routeros
/interface wireguard add name=wg-s12 listen-port=51812
/interface vlan add name=vlan112 vlan-id=112 interface=ether3
/ip vrf add name=vrf-s12 interfaces=wg-s12,vlan112
/ip address add address=10.250.0.1/16 interface=wg-s12
/ip address add address=192.168.1.254/24 interface=vlan112
/ip firewall nat add chain=srcnat out-interface=vlan112 action=masquerade
/ip firewall filter add chain=forward in-interface=vrf-s12 src-address=10.250.12.0/24 dst-address=192.168.1.0/24 action=accept
/ip firewall filter add chain=input in-interface=vrf-s12 src-address=10.250.12.0/24 dst-address=10.250.0.1 protocol=icmp action=accept
```

**PBR** (one port, routing rule per booking, gateways gw-N):

```routeros
/interface wireguard add name=wg0 listen-port=51820
/ip address add address=10.250.0.1/16 interface=wg0
/interface vlan add name=vlan199 vlan-id=199 interface=ether3
/ip address add address=10.199.0.1/24 interface=vlan199
/routing table add name=s12 fib
/ip route add dst-address=0.0.0.0/0 gateway=10.199.0.12 routing-table=s12
/ip firewall filter add chain=forward in-interface=wg0 out-interface=vlan199 action=accept
# per booking (driver peer add): the peer AND
/routing rule add src-address=10.250.12.1/32 interface=wg0 table=s12 action=lookup-only-in-table
```

**Serial/USB appliances, all modes** ([consoles-usb.md](consoles-usb.md)), read back from `vlabzone-vrf-ros`:

```routeros
/interface vlan add name=vlan197 vlan-id=197 interface=ether3
/ip address add address=10.197.0.1/24 interface=vlan197
# VRF + hybrid: route leak into each setup table (the driver waits for the asynchronous VRF table first)
/ip route add dst-address=10.197.0.0/24 gateway=vlan197@main routing-table=vrf-s12
# VRF only: replies to the setup's tunnels, into its VRF
/ip route add dst-address=10.250.12.0/24 gateway=wg-s12@vrf-s12
# PBR only: before the booking rules
/routing rule add dst-address=10.197.0.0/24 interface=wg0 action=lookup table=main
# one forward rule per console of the setup's devices (no ip.port sets on RouterOS), one per USB hub
/ip firewall filter add chain=forward in-interface=vrf-s12 src-address=10.250.12.0/24 out-interface=vlan197 \
    dst-address=10.197.0.10 protocol=tcp dst-port=4001 action=accept comment="vlab: console s12 10.197.0.10:4001"
/ip firewall filter add chain=forward in-interface=vrf-s12 src-address=10.250.12.0/24 out-interface=vlan197 \
    dst-address=10.197.0.11 protocol=tcp dst-port=18574 action=accept comment="vlab: usb s12 ausb1"
```

`out-interface=vlan197` works in the filter because vlan197 isn't a VRF member. In VRF mode `in-interface=vrf-sN`
also matches the setup VLAN, so the source prefix keeps the devices out again (E14c). `edge-ctl console allow`
inserts a rule before the final drop, and `deny` removes it and the console's connections.

**Firewall, all modes** (input; forward as above, then drop):

```routeros
/ip firewall filter
add chain=input connection-state=established,related action=accept
add chain=input connection-state=invalid action=drop
add chain=input in-interface=ether1 action=accept                          # containerlab management [sim]
add chain=input in-interface=ether4 protocol=tcp dst-port=22,80 action=accept   # broker over lab management
add chain=input in-interface=ether2 protocol=udp dst-port=51820 action=accept   # VRF: 51800-52099
add chain=input in-interface=wg0 dst-address=10.250.0.1 protocol=icmp action=accept   # VRF: per vrf-sN, see above
add chain=input action=drop
add chain=forward connection-state=established,related action=accept
add chain=forward connection-state=invalid action=drop
# … per-mode forward rules …
add chain=forward action=drop
```

**Per booking** (`peer add`): `/interface wireguard peers add interface=<wg-sN|wg0> public-key=… allowed-address=10.250.N.U/32`
(PBR also the routing rule). `peer del` removes it (and the rule) and deletes the tunnel's entries from
`/ip firewall connection`, like `conntrack -D` on Linux, so running sessions end at once.

---

## 3. Differences to the Linux edge

| | Linux edge | RouterOS edge | Consequence |
|---|---|---|---|
| Management interface | none (`network-mode: none`) | ether1 on the containerlab management network (vrnetlab needs it) | E05 "no management interface" is skipped for lab-edge with that reason. No other node is on that network, so it is no path past corp-fw. The input filter accepts ether1 only for the lab [sim]; on the real MikroTik management runs over ether4 / the lab management VLAN |
| Throughput | line rate | **1 Mbit/s per interface** (free CHR licence) | functional tests pass; E11 limits its two iperf3 load streams to 400 kbit/s each on RouterOS. Throughput experiments (E15, E17, E21, E22) need a licence |
| Activation of changes | immediate | filter and routing rules take effect after **~3 s**; a new VRF's interface and routing table appear **asynchronously** | the driver waits for the VRF interfaces and tables before referring to them, and 3 s after rebuilding the firewall. The broker driver must do the same (and check `invalid` flags) |
| Boot | seconds | REST up ~20–45 s after the container starts | `make up` takes ~1 min |
| Tests that change Linux internals (E10, `ip rule`, `nft`, `wg show`) | run | skipped | repeated through REST in E27 |

---

## 4. Finding: tunnel ping rule in VRF mode let setup devices reach the edge

The first run of the VRF lab failed E05b: a device in setup 12 (with the edge as gateway) could ping the edge's
tunnel address 10.250.0.1. The input rule was `in-interface=vrf-s12 dst-address=10.250.0.1 protocol=icmp accept`,
as in the stage 1b trial. Inside a VRF, the firewall sees the **VRF** as in-interface for traffic from the tunnel
**and** from the setup VLAN (members can't be named, [routeros-spike.md §3](routeros-spike.md)), so the rule
matched the devices too. The trial didn't test this path.

Fix: add the setup's tunnel prefix, `src-address=10.250.12.0/24`. Setup devices (192.168.1.x) are dropped, the
tunnels can still ping. The new address layout makes this a plain prefix ([tunnel-addresses.md](tunnel-addresses.md)).
On Linux the same rule uses `meta sdifname "wg-s*"` (the real ingress interface), which RouterOS has no equivalent for.
The forward rules got the same source prefix.

---

## 5. Results

`make test EDGE_IMPL=routeros EDGE_MODE=<mode>` (E01–E06, E09, E10 where possible, E11, E13, E14, E20, E26, E27):

| Lab | Passed | Skipped | Failed |
|---|---|---|---|
| `vlabzone-vrf-ros` | 80 | 13 | 0 |
| `vlabzone-pbr-ros` | 85 | 11 | 0 |
| `vlabzone-hybrid-ros` | 82 | 11 | 0 |

Skipped: E05 management interface of lab-edge (section 3), E26e kernel `usbip attach` (vhci-hcd), the E10 cases that change Linux internals, the
E10/E27 cases of the other modes, and the RouterOS-only check of E00.

**E27: E10's misconfigurations on RouterOS** ([`tests/test_e27_routeros_parity.py`](../tests/test_e27_routeros_parity.py)):

| Case | VRF | PBR | hybrid | Same as Linux? |
|---|---|---|---|---|
| Changes per booking | 1 | 2 | 1 | yes |
| Routing rule missing | – | fail-closed | fail-closed | yes |
| Rule points to the wrong setup | – | **foreign access** (setup 7) | **fail-closed**; control without the source prefix: setup 7 | yes |
| Forward rule of setup 12 missing | fail-closed | – | fail-closed | yes |

E14 (serial consoles) and E26 (USB) run unchanged through the driver: 11 passed, 1 skipped (kernel `usbip attach`)
in every mode, including the console regrouping and the "foreign console unreachable" checks.

Also through the driver, unchanged from Linux: address outside the setup prefix refused, booking on the wrong
setup fails closed, setups can't reach foreign tunnels (E10 tests that only use `edge-ctl`).

---

## Carried over to the real environment

1. The driver is the stage-3 broker driver for the MikroTik: one REST client for configuration and bookings.
   Before using it against the real router: its RouterOS version, its interface names (ether2/3/4 here) and
   management over the lab management VLAN instead of ether1.
2. Wait for asynchronous objects (VRF interfaces and tables) and for rule activation (~3 s); check `invalid` flags.
3. Every firewall rule that refers to a VRF also needs the setup's tunnel prefix as source (section 4).
4. Revoking a booking deletes the peer **and** the tunnel's connection-tracking entries.
