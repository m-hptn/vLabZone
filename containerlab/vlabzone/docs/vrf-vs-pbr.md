# VRF vs. PBR vs. hybrid

Three ways for the lab edge to keep setups with identical networks apart. All are built as complete labs
([lab-vrf.md](lab-vrf.md), [lab-pbr.md](lab-pbr.md), [lab-hybrid.md](lab-hybrid.md)) and run against the same
test suite (`make test-all`). All three were also tried on a MikroTik CHR ([routeros-spike.md](routeros-spike.md)).

**Constraint that decides it:** the corporate firewall should need **one rule with one port**. VRF needs a port
range (one per setup). PBR and hybrid need one port.

## 1. How they differ

| | VRF | PBR | hybrid |
|---|---|---|---|
| Separation | one VRF per setup on the edge | one routing table per setup → gateway gw-N | one VRF per setup on the edge |
| WireGuard on the edge | one interface and port per setup (51800 + N) | one `wg0`, port 51820 | one `wg0`, port 51820 |
| What selects the setup | the UDP port the workspace connects to | `ip rule` per booking → gateway | `ip rule` per booking → the VRF's table |
| Setup gateway (`.254`), MASQUERADE | on the edge | on a gateway gw-N per setup | on the edge |
| Edge trunk | every setup VLAN | only the transfer VLAN 199 | every setup VLAN |
| Changes on the edge per booking | 1 (peer) | 2 (peer + rule) | 2 (peer + rule); **1 with setup-grouped addresses** (section 5) |
| Changes on the edge per new setup | VRF, VLAN interface, WireGuard interface, firewall + NAT rule | a routing table entry (plus a gateway) | VRF, VLAN interface, route back to `wg0`, firewall + NAT rule |
| **Corporate allowance** | UDP port range 51800–52099 (300 setups) | **UDP 51820** | **UDP 51820** |

## 2. Test results

All three labs pass every functional test (E01–E06, E09, E20). E01 also checks the allowance per mode.
E10 adds the differences (`tests/test_e10_vrf_vs_pbr.py`, protocols in `results/E10-*.json`):

| Case | VRF | PBR | hybrid |
|---|---|---|---|
| Objects changed per booking | 1 | 2 | 2 |
| Booking registered on the wrong setup / rule missing | **fail-closed** | **fail-closed** | **fail-closed** |
| Rule into a table without route | – | **fail-closed** (catch-all `iif wg0 unreachable`, now in both rule modes) | **fail-closed** (same) |
| Booking rule points to the wrong setup's table | – | **two-way access to the wrong setup** | **fail-closed**: per-setup forward rule checks the last octet of the tunnel address (control without it: access to the wrong setup) |
| Wrong tunnel address + matching rule (consistent broker mistake) | – (wrong peer on wrong port: foreign access too) | foreign access | foreign access |
| Setup-level mistake: tunnel interface in the wrong VRF + matching forward rule | **one-way leak** | – | – |
| Setups reaching each other's tunnels | blocked | blocked by the edge forward filter | blocked by the edge forward filter |
| Setups sharing a segment | no | **yes** (transfer VLAN 199, isolated by the gateway firewalls) | no |
| Edge addresses reachable from tunnels | only the VRF's own addresses | every main address via the `local` table; restricted by the input filter | same as PBR |
| Interface change (`wg-sN` / `wg0` restart) | interrupts one setup (12–16 s) | interrupts everyone | interrupts everyone |

**On RouterOS 7.24.6** (CHR trial):

| | VRF | PBR | hybrid |
|---|---|---|---|
| Checks | 25/25 | 25/25 | 26/26 |
| Edge services from tunnels without filter | not reachable | reachable | reachable |
| Setup route missing | – | fail-closed only with `action=lookup-only-in-table` | same |
| Firewall per setup | `in-interface=vrf-sN` | `in-interface=wg0` (setups not told apart; the gateways do) | mangle mark on `out-interface=vlanN`, filter on `packet-mark` |
| Safeguard "address must match the setup" | – | – | **not possible with 10.250.\<user\>.\<setup\>** (no last-octet match); works with 10.250.\<setup\>.\<user\> |

## 3. Advantages of each

| | Advantages | Drawbacks |
|---|---|---|
| **VRF** | per-booking mistakes fail closed by construction; restarting a WireGuard interface affects one setup; no shared segment | a port per setup: the corporate rule is a range, and it limits the number of setups |
| **PBR** | one port; edge doesn't touch the setups (gateways can come with the setup, e.g. on Proxmox); NAT load spread over gateways | a gateway per setup; shared transfer VLAN; a wrong rule gives foreign access, nothing catches it on the edge |
| **hybrid** | one port; no gateways, NAT on the edge as in VRF; no shared segment; a wrong rule fails closed (Linux; RouterOS with setup-grouped addresses) | `wg0` restart interrupts everyone; edge services visible from tunnels without filter; per-booking rule (unless addresses are grouped by setup) |

## 4. Recommendation

With the one-port constraint: **hybrid**. It keeps the setups in VRFs on the edge (no gateways, no shared
segment) and has PBR's single port. Its weak point, the per-booking rule, is covered by the per-setup safeguard.

**Tunnel address scheme:** switching from 10.250.\<user\>.\<setup\> (idea.md §4.3) to
**10.250.\<setup\>.\<user\>** makes the hybrid simpler and safer on both platforms:

- the routing rule becomes **static per setup** (`from 10.250.12.0/24 iif wg0 lookup 1012`): a booking is only the peer, as in VRF;
- the safeguard becomes a plain prefix match, which RouterOS can express (shown in the trial, section 6 of
  [routeros-spike.md](routeros-spike.md#6-hybrid-design-on-routeros));
- limits: 255 setups (needs `/24` per setup inside 10.250.0.0/16) and 254 users per setup.

What remains per booking is the peer's allowed address. A broker mistake there (an address from the wrong setup)
leads into that setup. That is the same class of mistake as a VRF peer on the wrong interface, and the broker's
reconcile loop (stage 3) compares it with the bookings.

Without the one-port constraint, VRF stays the simplest safe choice. PBR has no advantage left over hybrid in
this environment except moving NAT and setup gateways off the edge.

Open points:
- **Address scheme:** decide 10.250.\<setup\>.\<user\> (hybrid with static rules) – affects broker, agent, tests.
- **E21 (scale):** 50 setups / 200 contexts in each mode.
- **Real MikroTik:** repeat the trial with its RouterOS version.
