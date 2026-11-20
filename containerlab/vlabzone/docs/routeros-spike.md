# Stage 1b – RouterOS trial (MikroTik CHR as lab edge)

**Question:** can a MikroTik running RouterOS do what the Linux edge does in the VRF lab: one VRF per setup with
a WireGuard interface and a VLAN interface, the same setup network in several VRFs, MASQUERADE, and a firewall
that only lets "tunnel N → setup N" through? And can the broker drive it through the REST API?

**Answer: yes**, with one important difference in how firewall rules refer to interfaces inside a VRF
(section 3), and one hardening step that a stock RouterOS does not have (section 4).

| | |
|---|---|
| RouterOS | **7.24.6 (long-term)** as Cloud Hosted Router (CHR) in containerlab (`mikrotik_ros`, vrnetlab image). To be repeated with the version of the real MikroTik |
| Lab | [`topology/spike-routeros.clab.yml`](../topology/spike-routeros.clab.yml): `ws` → CHR → `sw` (trunk 112, 107) → `dev12`, `dev7` (both 192.168.1.20) |
| Script | [`scripts/spike-routeros.py`](../scripts/spike-routeros.py): configures the CHR via REST, runs 25 checks, writes `results/spike-routeros-*.json` |
| Result | **25 of 25 checks pass**, on a freshly booted CHR and again on an already configured one |

```bash
make build-routeros          # once per host: CHR image via vrnetlab (needs /dev/kvm to run)
make spike-up                # deploy the trial lab (~45 s until RouterOS answers)
.venv/bin/python scripts/spike-routeros.py
make spike-down
```

---

## 1. Results

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

## 2. Configuration (equivalent RouterOS CLI)

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

## 5. Consequences for the plan

1. The RouterOS edge can follow the **VRF design** of the Linux edge. Nothing found in the trial pushes towards PBR.
2. The broker driver `edge_routeros.py` (stage 3) uses the REST API as shown in section 2. It must
   wait for or verify rule activation, and check `invalid` flags during reconcile.
3. Startup config for the RouterOS edge in the lab (stage 2): generate the commands of section 2 from `setups.yaml`.
4. Open: repeat with the **RouterOS version of the real MikroTik**; check the PBR variant (`/routing rule`) only if
   PBR becomes relevant again; MTU/throughput only with a CHR licence (free licence: 1 Mbit/s per interface).
