# Stage 1b – RouterOS trial (MikroTik CHR as lab edge)

**Question:** can a MikroTik running RouterOS do what the Linux edge does in the two labs, in the **VRF design**
(one VRF per setup with a WireGuard and a VLAN interface), in the **PBR design** (one WireGuard interface,
a routing rule per booking, a gateway per setup) and in the **hybrid design** (one WireGuard interface, routing
rules into setup VRFs on the router)? Overlapping setup networks, MASQUERADE, a firewall that only lets
"tunnel → own setup" through, and everything driven by the broker through the REST API?

**Answer: yes, all three designs work**, with four things to know:
- how firewall rules refer to interfaces inside a VRF (section 3),
- the routing-rule action in PBR, which decides whether a missing setup route fails closed (section 5),
- a stock RouterOS exposes its management, from the corporate side and, in PBR/hybrid, from the tunnels (section 4),
- in the hybrid design the firewall can't tell the setups apart in the filter table, and can't match the last octet
  of a tunnel address. Mangle marks solve the first; the second needs tunnel addresses grouped by setup (section 6).

| | |
|---|---|
| RouterOS | **7.24.6 (long-term)** as Cloud Hosted Router (CHR) in containerlab (`mikrotik_ros`, vrnetlab image). To be repeated with the version of the real MikroTik |
| Labs | VRF: [`spike-routeros.clab.yml`](../topology/spike-routeros.clab.yml): `ws` → CHR → `sw` (trunk 112, 107) → `dev12`, `dev7` (both 192.168.1.20)<br>PBR: [`spike-routeros-pbr.clab.yml`](../topology/spike-routeros-pbr.clab.yml): `ws` → CHR → `sw` (trunk 199) → `gw-12`, `gw-7` (the Linux gateways of the PBR lab) → `dev12`, `dev7`<br>hybrid: [`spike-routeros-hybrid.clab.yml`](../topology/spike-routeros-hybrid.clab.yml): wired like the VRF trial, own lab so the CHR starts clean |
| Script | [`scripts/spike-routeros.py`](../scripts/spike-routeros.py) `--mode vrf\|pbr\|hybrid`: configures the CHR via REST, runs the checks, writes `results/spike-routeros-<mode>-*.json`. Every run starts without filter rules |
| Result | **VRF: 25 of 25, PBR: 25 of 25, hybrid: 26 of 26 checks pass**, each on a freshly booted CHR and again on an already configured one |

```bash
make build-routeros                 # once per host: CHR image via vrnetlab (needs /dev/kvm to run)
make spike-up     SPIKE=vrf|pbr|hybrid   # deploy a trial lab (~45 s until RouterOS answers)
make spike-test   SPIKE=vrf|pbr|hybrid   # configure via REST + checks
make spike-down   SPIKE=vrf|pbr|hybrid
```

---

## 1. Results – VRF design

| Check | Result |
|---|---|
| containerlab interface mapping | `ether1` = management (vrnetlab), clab `eth1` = `ether2`, `eth2` = `ether3` |
| Boot time | REST API answers **42–44 s** after the container starts. RAM: ~175 MB |
| VRF per setup with WireGuard + VLAN interface | works (`/ip vrf` with `interfaces=wg-s12,vlan112`) |
| Same `192.168.1.254/24` in two VRFs | accepted |
| **WireGuard interface inside a VRF**, handshake on the main-VRF address | **works**: the main risk before the trial |
| Setup 12 and setup 7, same IP 192.168.1.20 | each tunnel reaches its own device |
| MASQUERADE per setup VLAN | works, devices see 192.168.1.254 |
| Spoofed tunnel source address | dropped (WireGuard cryptokey routing, `allowed-address=/32`) |
| Setup → corporate side | blocked by the VRF (no route), even without firewall rules |
| Edge services from inside a setup VRF | not reachable: they listen in VRF `main` only |
| **Edge services from the corporate side, stock config** | **open**: FTP, SSH, Telnet, HTTP/REST, Winbox, API (section 4) |
| Firewall rules on VRF member interfaces | **flagged invalid** (section 3) |
| Edge firewall with `in-interface=vrf-sN` | works; negative control: without setup 12's rule, setup 12 is closed and setup 7 unaffected |
| Firewall rules take effect | **~3 s** after they were created via REST |
| Revoke: delete the peer via REST | the running tunnel gets no answer any more |

---

## 2. Configuration – VRF design (equivalent RouterOS CLI)

The script uses the REST API. This is the same configuration as CLI commands, for one setup. Setup 7 is identical
with 7 / 107 / 51807.

```routeros
/ip address add address=10.20.10.1/24 interface=ether2                 # corporate side
/interface vlan add name=vlan112 vlan-id=112 interface=ether3          # trunk to the switches
/interface wireguard add name=wg-s12 listen-port=51812
/ip vrf add name=vrf-s12 interfaces=wg-s12,vlan112
/ip address add address=192.168.1.254/24 interface=vlan112             # same address in every VRF
/ip address add address=10.250.0.1/16 interface=wg-s12
/ip firewall nat add chain=srcnat out-interface=vlan112 action=masquerade

# per booking (broker): one peer, /32 = cryptokey routing
/interface wireguard peers add interface=wg-s12 public-key="<key>" allowed-address=10.250.1.12/32

# edge firewall (section 3 + 4)
/ip firewall filter
add chain=input connection-state=established,related action=accept
add chain=input connection-state=invalid action=drop
add chain=input in-interface=ether1 action=accept comment="management (lab: REST/SSH)"
add chain=input in-interface=ether2 protocol=udp dst-port=51800-52099 action=accept comment="WireGuard"
add chain=input in-interface=vrf-s12 dst-address=10.250.0.1 protocol=icmp action=accept
add chain=input action=drop
add chain=forward connection-state=established,related action=accept
add chain=forward connection-state=invalid action=drop
add chain=forward in-interface=vrf-s12 src-address=10.250.0.0/16 dst-address=192.168.1.0/24 action=accept
add chain=forward action=drop
```

REST equivalents, as the broker would use them (HTTP basic auth):

| Action | REST call |
|---|---|
| grant access | `PUT /rest/interface/wireguard/peers {"interface":"wg-s12","public-key":"…","allowed-address":"10.250.1.12/32"}` |
| revoke | `DELETE /rest/interface/wireguard/peers/<.id>` |
| reconcile | `GET /rest/interface/wireguard/peers`, `GET /rest/ip/firewall/filter` (check `invalid`) |

---

## 3. Finding: firewall rules inside a VRF (RouterOS counterpart of `meta sdifname`)

For traffic in a VRF, RouterOS's firewall sees the **VRF interface** (`vrf-s12`) as the input interface, much
like Linux. RouterOS goes further, though:

| Rule refers to | RouterOS 7.24.6 |
|---|---|
| `in-interface=vrf-s12` | valid, **matches** (input and forward) |
| `in-interface=wg-s12` or `in-interface=vlan112` (VRF member) | rule flagged **invalid**, never matches |
| `out-interface=vlan112` (VRF member) together with `in-interface=vrf-s12` | whole rule flagged **invalid** |
| `in-interface-list` that contains VRF interfaces | shown as **valid**, but **never matches**, silently |

Consequences for the real edge:

- **Name only the VRF in rules**, combined with addresses: "tunnel → setup" becomes
  `in-interface=vrf-sN src-address=10.250.0.0/16 dst-address=<setup net>`. Within one VRF only that setup's
  WireGuard and VLAN interface exist, so this is as precise as "wg-sN → vlanN".
- **No interface lists with VRFs.** They look correct in the configuration and do nothing. One rule per VRF instead.
- **Check `invalid` after every change.** An invalid accept rule fails closed here (the final drop catches the
  traffic). An invalid **drop** rule, however, would silently let traffic through. The broker's reconcile loop
  should read `/rest/ip/firewall/filter` and alarm on `invalid=true`.

How the trial got here (it took three attempts): the first firewall version used `in-interface=wg-s12`.
Tunnel traffic seemed to pass, because the script probed before RouterOS had activated the new rules
(about 3 s), so that PASS was false. Rule counters and a negative control exposed it. The script now waits until
the firewall is in effect, and verifies rules with counters and a negative control.

---

## 4. Finding: a stock RouterOS exposes its management on every main-VRF address

Without a filter, the CHR answered on its corporate address (10.20.10.1) with FTP, SSH, Telnet, HTTP/REST, Winbox
and API. The Linux edge drops everything there except WireGuard. For the real MikroTik:

- input filter: from the corporate side only UDP to the WireGuard ports; management only from the management
  network (in the lab: `ether1`);
- disable the services that aren't needed (`/ip service disable ftp,telnet,api,…`), use `www-ssl` for REST with a
  dedicated broker user instead of `admin`, restricted to the management address (`/ip service set www-ssl address=…`);
- inside the setup VRFs the services were not reachable (they listen in `main`); keep it that way, and don't move
  services into a setup VRF.

---

## 5. PBR design on RouterOS

**Configuration** (equivalent CLI; the gateways `gw-12` / `gw-7` are the Linux gateways of the PBR lab):

```routeros
/ip address add address=10.20.10.1/24 interface=ether2
/interface vlan add name=vlan199 vlan-id=199 interface=ether3          # transfer VLAN only on the trunk
/ip address add address=10.199.0.1/24 interface=vlan199
/interface wireguard add name=wg0 listen-port=51820                    # one port for every setup
/ip address add address=10.250.0.1/16 interface=wg0
/routing table add name=s12 fib
/ip route add dst-address=0.0.0.0/0 gateway=10.199.0.12 routing-table=s12

# per booking (broker): TWO changes
/interface wireguard peers add interface=wg0 public-key="<key>" allowed-address=10.250.1.12/32
/routing rule add src-address=10.250.1.12/32 interface=wg0 table=s12 action=lookup-only-in-table

/ip firewall filter
add chain=input connection-state=established,related action=accept
add chain=input connection-state=invalid action=drop
add chain=input in-interface=ether1 action=accept comment="management (lab: REST/SSH)"
add chain=input in-interface=ether2 protocol=udp dst-port=51820 action=accept
add chain=input in-interface=wg0 dst-address=10.250.0.1 protocol=icmp action=accept
add chain=input action=drop
add chain=forward connection-state=established,related action=accept
add chain=forward connection-state=invalid action=drop
add chain=forward in-interface=wg0 out-interface=vlan199 action=accept   # which gateway: routing rules
add chain=forward action=drop
```

REST for the broker: `PUT /rest/interface/wireguard/peers` **and** `PUT /rest/routing/rule`
`{"src-address":"10.250.1.12/32","interface":"wg0","table":"s12","action":"lookup-only-in-table"}`; revoke deletes both.

**Results**

| Check | Result |
|---|---|
| Routing tables with a default route via the gateway | active; gateway resolved via the connected transfer net |
| Booking = peer + routing rule | 2 changes per booking (VRF: 1) |
| Setup 12 and setup 7 over **one** port 51820, same IP 192.168.1.20 | each reaches its own device via its gateway |
| MASQUERADE on the gateway, spoofing | devices see .254; spoofed source dropped |
| Routing rule missing | **fail-closed**: main table has no route into a setup |
| Routing rule points to the wrong table | **access to the foreign setup**, as on Linux (E10) |
| **Edge services from a tunnel, no filter** | **reachable**: SSH/HTTP on 10.250.0.1, 10.199.0.1 and 10.20.10.1. In the VRF design they were not (the tunnels live in a setup VRF, the services in `main`) |
| Firewall with `in-interface=wg0` / `out-interface=vlan199` | valid and working: `wg0` is not in a VRF, so the interface rules of section 3 don't apply |
| With firewall | services closed from corporate side and tunnels; tunnel gateway ping allowed; setups reach neither edge, other gateway, tunnels nor corporate net; negative control and revoke as expected |

**Finding: the routing-rule action decides what happens when a setup route is missing**

| Rule action | Setup table has its route | Setup table route missing (gateway removed, misconfiguration) |
|---|---|---|
| `lookup-only-in-table` | to the gateway | **fail-closed**: unreachable |
| `lookup` | to the gateway | **falls back to the main table: the tunnel reached the corporate network** (workspace 10.20.10.11, edge SSH on 10.20.10.1) |
| `lookup` + edge firewall | to the gateway | blocked by the forward rule (`wg0` → `vlan199` only) |

So for PBR on RouterOS: **always `action=lookup-only-in-table`**, and the forward rule as a second line of
defence. Linux has the same fall-through (`ip rule … lookup` continues with the next rule when the table has no
route); the Linux PBR edge is protected by its forward rule. A catch-all `ip rule add iif wg0 unreachable` after the
per-booking rules would make Linux fail closed without relying on the firewall.

---

## 6. Hybrid design on RouterOS

One `wg0` in `main` (one port, as PBR), the setups as VRFs on the router (as VRF), routing rules from the
tunnel address into the VRF's table. The Linux counterpart is the lab `vlabzone-hybrid` ([lab-hybrid.md](lab-hybrid.md)).

**Configuration** (equivalent CLI)

```routeros
/ip address add address=10.20.10.1/24 interface=ether2
/interface wireguard add name=wg0 listen-port=51820                    # one port for every setup
/ip address add address=10.250.0.1/16 interface=wg0
/interface vlan add name=vlan112 vlan-id=112 interface=ether3
/ip vrf add name=vrf-s12 interfaces=vlan112                            # only the VLAN, not the WireGuard
/ip address add address=192.168.1.254/24 interface=vlan112
/ip firewall nat add chain=srcnat out-interface=vlan112 action=masquerade
/ip route add dst-address=10.250.0.0/16 gateway=wg0@main routing-table=vrf-s12   # way back to the tunnels

# per booking (broker): TWO changes
/interface wireguard peers add interface=wg0 public-key="<key>" allowed-address=10.250.1.12/32
/routing rule add src-address=10.250.1.12/32 interface=wg0 table=vrf-s12 action=lookup-only-in-table

# firewall: the setup can only be identified through a mangle mark (see findings)
/ip firewall mangle add chain=forward out-interface=vlan112 action=mark-packet new-packet-mark=to-s12 passthrough=yes
/ip firewall filter
add chain=input connection-state=established,related action=accept
add chain=input connection-state=invalid action=drop
add chain=input in-interface=ether1 action=accept comment="management (lab: REST/SSH)"
add chain=input in-interface=ether2 protocol=udp dst-port=51820 action=accept
add chain=input in-interface=wg0 dst-address=10.250.0.1 protocol=icmp action=accept
add chain=input action=drop
add chain=forward connection-state=established,related action=accept
add chain=forward connection-state=invalid action=drop
add chain=forward in-interface=wg0 packet-mark=to-s12 action=accept    # + src-address=10.250.12.0/24, see variant
add chain=forward action=drop
```

**Results**

| Check | Result |
|---|---|
| Route back `10.250.0.0/16 gateway=wg0@main` in each VRF table | active. Right after `/ip vrf add`, REST rejects `routing-table=vrf-sN` as unknown for a moment: the VRF's table appears asynchronously, the script waits for it |
| Booking = peer + routing rule into `vrf-s12` | 2 changes per booking |
| Setup 12 and setup 7 over one port, same IP 192.168.1.20 | each reaches its own device; MASQUERADE in the VRF works (devices see .254) |
| Spoofed source | dropped (cryptokey routing) |
| Routing rule missing | **fail-closed** |
| Routing rule points to the wrong VRF (no firewall, and with the firewall above) | **access to the foreign setup**, as in PBR |
| Edge services from a tunnel, no filter | **reachable** (SSH/HTTP on 10.250.0.1 and 10.20.10.1), as in PBR: `wg0` is in `main` |
| With firewall | services closed, setups work through their forward rules, setup 12 reaches neither tunnels, corporate net nor edge |

**Findings: how the firewall sees traffic from `wg0` into a VRF**

| Rule | Valid? | Matches? |
|---|---|---|
| filter `out-interface=vlan112` (VRF member) | **invalid** | – |
| filter `out-interface=vrf-s12` | valid | **never** (0 packets) |
| filter `in-interface=wg0` / `dst-address=192.168.1.0/24` | valid | yes, but these don't tell the setups apart |
| **mangle** forward `out-interface=vlan112` | valid | **yes**: mark the packet per setup there, filter on `packet-mark` |
| `src-address=10.250.0.12/255.255.0.255` (last octet) | accepted, but RouterOS turns it into the **range** 10.250.0.12–10.250.255.12 | not a last-octet match |

So the Linux safeguard of the hybrid lab (tunnel address `10.250.<user>.N` may only enter setup N) **can't be
written on RouterOS** with the current address scheme.

**Variant: tunnel addresses 10.250.\<setup\>.\<user\>**

With the setup in the third octet, each setup is a prefix (`10.250.12.0/24`), and RouterOS can match it:

```routeros
# once per setup – no routing rule per booking any more
/routing rule add src-address=10.250.12.0/24 interface=wg0 table=vrf-s12 action=lookup-only-in-table
/ip firewall filter add chain=forward in-interface=wg0 packet-mark=to-s12 src-address=10.250.12.0/24 action=accept

# per booking: ONE change
/interface wireguard peers add interface=wg0 public-key="<key>" allowed-address=10.250.12.1/32
```

| Check | Result |
|---|---|
| Changes per booking | **1** (only the peer), as in VRF |
| Setups 12 and 7 via the static prefix rules | both reachable |
| Static rule pointing to the wrong VRF | **fail-closed**: dropped by the prefix firewall rule |
| A setup-12 key sending from a setup-7 address | dropped (cryptokey routing) |
| Revoke (delete the peer) | access ends, setup 7 unaffected |

What remains per booking is the peer's `allowed-address`: a peer registered with an address from the wrong
setup's prefix leads into that setup. In the VRF design the same mistake is the peer on the wrong `wg-sN`.

---

## 7. Consequences for the plan

1. The RouterOS edge can follow every design. Nothing in the trial pushes towards PBR; in PBR (and hybrid)
   the tunnels see the router's own services in `main`, so the input filter is mandatory.
2. If the corporate firewall allows only **one port**: hybrid with tunnel addresses grouped by setup
   (10.250.\<setup\>.\<user\>): one port, setups in VRFs, one change per booking, and a wrong setup rule fails
   closed on Linux and RouterOS. Otherwise VRF ([vrf-vs-pbr.md](vrf-vs-pbr.md)).
3. The broker driver `edge_routeros.py` (stage 3) uses the REST API as shown in section 2. It must
   wait for or verify rule activation, and check `invalid` flags during reconcile.
4. Startup config for the RouterOS edge in the lab (stage 2): generate the commands of section 2 from `setups.yaml`.
5. If PBR is chosen anyway: `action=lookup-only-in-table` for every rule, firewall as above, and the broker's
   reconcile loop compares rules (table!) with the bookings.
6. Open: repeat with the **RouterOS version of the real MikroTik**; MTU/throughput only with a CHR licence
   (free licence: 1 Mbit/s per interface).
