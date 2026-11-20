# VRF vs. PBR vs. hybrid

Three ways for the lab edge to keep setups with identical networks apart. All are built as complete labs
([lab-vrf.md](lab-vrf.md), [lab-pbr.md](lab-pbr.md), [lab-hybrid.md](lab-hybrid.md)) and run against the same
test suite (`make test-all`). All three were also tried on a MikroTik CHR ([routeros-spike.md](routeros-spike.md)).

**Constraint that decides it:** the corporate firewall should need **one rule with one port**. VRF needs a port
range (one per setup). PBR and hybrid need one port.

**Tunnel addresses** are 10.250.\<setup\>.\<user\> in every mode, so each setup owns the prefix
10.250.\<setup\>.0/24. Why: [tunnel-addresses.md](tunnel-addresses.md).

## 1. How they differ

| | VRF | PBR | hybrid |
|---|---|---|---|
| Separation | one VRF per setup on the edge | one routing table per setup → gateway gw-N | one VRF per setup on the edge |
| WireGuard on the edge | one interface and port per setup (51800 + N) | one `wg0`, port 51820 | one `wg0`, port 51820 |
| What selects the setup | the UDP port the workspace connects to | `ip rule` **per booking** (`from 10.250.12.1`) → gateway | `ip rule` **per setup** (`from 10.250.12.0/24`) → the VRF's table |
| Setup gateway (`.254`), MASQUERADE | on the edge | on a gateway gw-N per setup | on the edge |
| Edge trunk | every setup VLAN | only the transfer VLAN 199 | every setup VLAN |
| Changes on the edge per booking | **1** (peer) | 2 (peer + rule) | **1** (peer) |
| Changes on the edge per new setup | VRF, VLAN interface, WireGuard interface, firewall + NAT rule | a routing table entry (plus a gateway) | VRF, VLAN interface, route back to `wg0`, setup rule, firewall + NAT rule |
| **Corporate allowance** | UDP port range 51800–52099 (300 setups) | **UDP 51820** | **UDP 51820** |

## 2. Test results

All three labs pass every functional test (E01–E06, E09, E20). E01 also checks the allowance per mode.
E10 adds the differences (`tests/test_e10_vrf_vs_pbr.py`, protocols in `results/E10-*.json`):

| Case | VRF | PBR | hybrid |
|---|---|---|---|
| Objects changed per booking | 1 | 2 | 1 |
| Address outside the setup's prefix (`--setup 12 --ip 10.250.7.1`) | refused by `edge-ctl` | refused | refused |
| Booking registered on the wrong setup (`--setup 7 --ip 10.250.7.1`, agent uses setup 12) | **fail-closed** | **fail-closed** | **fail-closed** |
| Rule missing | – | **fail-closed** | **fail-closed** |
| Rule into a table without route | – | **fail-closed** (catch-all `iif wg0 unreachable`) | **fail-closed** (same) |
| Rule points to the wrong setup's table | – | per **booking**: **two-way access to the wrong setup** | per **setup**: **fail-closed**, the forward rule allows only 10.250.N.0/24 into setup N (control without it: access to the wrong setup) |
| Setup-level mistake: tunnel interface in the wrong VRF + matching forward rule | **one-way leak** | – | – |
| Setups reaching each other's tunnels | blocked | blocked by the edge forward filter | blocked by the edge forward filter |
| Setups sharing a segment | no | **yes** (transfer VLAN 199, isolated by the gateway firewalls) | no |
| Edge addresses reachable from tunnels | only the VRF's own addresses | every main address via the `local` table; restricted by the input filter | same as PBR |
| Interface change (`wg-sN` / `wg0` restart) | interrupts one setup (12–16 s) | interrupts everyone | interrupts everyone |

**On RouterOS 7.24.6** (CHR trial):

| | VRF | PBR | hybrid |
|---|---|---|---|
| Edge services from tunnels without filter | not reachable | reachable | reachable |
| Setup route missing | – | fail-closed only with `action=lookup-only-in-table` | same |
| Firewall per setup | `in-interface=vrf-sN` | `in-interface=wg0` (setups not told apart; the gateways do) | mangle mark on `out-interface=vlanN`, filter on `packet-mark` + `src-address=10.250.N.0/24` |
| Wrong setup rule | – | foreign access | **fail-closed** |

## 3. Advantages and drawbacks

| | Advantages | Drawbacks |
|---|---|---|
| **VRF** | per-booking mistakes fail closed by construction; restarting a WireGuard interface affects one setup; no shared segment | a port per setup: the corporate rule is a range, and it limits the number of setups |
| **PBR** | one port; edge doesn't touch the setups (gateways can come with the setup, e.g. on Proxmox); NAT load spread over gateways | a gateway per setup; shared transfer VLAN; a rule per booking, and a wrong one gives foreign access with nothing on the edge to catch it |
| **hybrid** | one port; one change per booking; no gateways, NAT on the edge; no shared segment; a wrong setup rule fails closed on Linux and RouterOS | `wg0` restart interrupts everyone; edge services visible from tunnels without input filter |

## 4. Recommendation

**Hybrid**, with tunnel addresses 10.250.\<setup\>.\<user\>. It meets the one-port constraint, keeps the setups
in VRFs on the edge (no gateways, no shared segment), and a booking is a single WireGuard peer, as in VRF.
The rule that selects a setup is written once per setup and checked by a per-setup firewall rule, on both
the Linux edge and the MikroTik.

What remains per booking is the peer's allowed address. `edge-ctl` refuses addresses outside the setup's
prefix, so a mistake needs the wrong setup in two places (registration and agent). The broker's reconcile loop
(stage 3) compares peers with bookings.

Without the one-port constraint, VRF stays equally safe and slightly simpler (no rules at all). PBR has no
advantage left over hybrid in this environment, except moving NAT and the setup gateways off the edge.

Open points:
- **E21 (scale):** 50 setups / 200 contexts in each mode.
- **Real MikroTik:** repeat the trial with its RouterOS version.
