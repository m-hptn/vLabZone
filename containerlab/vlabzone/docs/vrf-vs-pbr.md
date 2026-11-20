# VRF vs. PBR

Two ways for the lab edge to keep setups with identical networks apart. Both are built as complete labs
([lab-vrf.md](lab-vrf.md), [lab-pbr.md](lab-pbr.md)) and run against the same test suite (`make test-all`).

## 1. How they differ

| | VRF | PBR |
|---|---|---|
| Separation | one VRF (routing table) per setup on the edge | one routing table per setup, selected per tunnel address by an `ip rule` |
| WireGuard on the edge | one interface and port per setup (51800 + N) | one interface `wg0`, port 51820 |
| What selects the setup | the UDP port the workspace connects to | the `ip rule` for the tunnel source address |
| Setup gateway (`.254`), MASQUERADE | on the edge | on a separate gateway gw-N per setup |
| Edge trunk | every setup VLAN | only the transfer VLAN 199 |
| Changes on the edge per booking | 1 (WireGuard peer) | 2 (WireGuard peer + ip rule) |
| Changes on the edge per new setup | VRF, VLAN interface, WireGuard interface, firewall + NAT rule | one routing table entry (plus a new gateway) |
| Corporate allowance | UDP port range (51800–52099: 300 setups) | UDP 51820 |

## 2. Test results

Both labs pass every functional test: E01–E06 and E09. E10 adds the differences
(`tests/test_e10_vrf_vs_pbr.py`, protocols in `results/E10-*.json`):

| Case | VRF | PBR |
|---|---|---|
| Objects changed per booking | 1 | 2 |
| Booking registered on the wrong setup / rule missing | **fail-closed** | **fail-closed** (rule missing) |
| Booking rule points to the wrong table | – (no per-booking routing) | **two-way access to the wrong setup** |
| Setup-level mistake: tunnel interface in the wrong VRF + matching forward rule | **one-way leak** (requests arrive, replies are dropped) | – |
| Gateways / setups reaching each other over the transfer VLAN | – (no shared segment) | blocked by the gateway firewalls |
| Edge addresses reachable from tunnels | only the VRF's own addresses | every edge address through the `local` table, unless the firewall restricts by destination (done now) |

Also measured: changing a WireGuard interface (VRF membership, down/up) drops all its sessions; workspaces
recover after **12–16 s**. In VRF this affects one setup, in PBR every user at once (single `wg0`).

## 3. Advantages of PBR

| Advantage | Why it matters | Status |
|---|---|---|
| **One UDP port** | the corporate firewall allowance is a single port instead of a range; easier to get approved, simpler with NAT in front of the edge | shown in the lab |
| **No limit from the port range** | VRF ties each setup to a port; growing beyond the range means asking the corporate team again | design fact |
| **Fewer interfaces on the edge** | VRF: three interfaces per setup (150 for 50 setups). PBR: one `wg0` and one transfer VLAN | not measured yet (E21) |
| **Edge doesn't touch the setups** | gateways can be created together with a setup (e.g. Proxmox SDN gateway or a small container); adding a setup barely changes the edge | shown: edge trunk carries only VLAN 199 |
| **Load spread out** | NAT and connection tracking run on the gateways, not all on the edge | not measured (E22) |
| **Switching setups without reconnecting** | changing the rule moves a user; tunnel, key and port stay. VRF needs a new handshake on another port | possible, not tested |
| **Platform support** | policy routing is supported almost everywhere; VRF together with WireGuard and firewall rules varies | RouterOS 7.24.6 supports the VRF design (stage 1b) – not an argument for PBR here |

## 4. Advantages of VRF

| Advantage | Why it matters |
|---|---|
| **Per-booking mistakes fail closed** | the only per-booking change is a WireGuard peer on the setup's own interface; a wrong one means no access, never foreign access |
| **Isolation by separate routing tables** | a packet from `wg-s12` cannot be looked up anywhere but in setup 12's table; nothing per booking decides that |
| **No shared segment between setups** | PBR's gateways share VLAN 199 and see all tunnel addresses |
| **Fewer moving parts** | no extra gateway per setup, NAT in one place |
| **Interface changes affect one setup** | restarting `wg-s12` interrupts only setup 12's users; restarting `wg0` interrupts everyone |

## 5. Recommendation

If the edge platform supports VRF with WireGuard properly, **VRF is the safer default**: isolation doesn't
depend on any per-booking configuration being right.

PBR is the better choice if the single firewall port is a hard requirement, if the number of setups will
grow beyond a few hundred, or if the MikroTik can't do VRF with WireGuard properly. Then the broker's
reconcile loop (stage 3) must check every ip rule against the bookings, because that is where a mistake
gives foreign access.

A middle ground, if the single port matters most: one `wg0` whose ip rules point directly into the setup VRFs.
That keeps one port and drops the gateways, but has PBR's failure mode (one wrong rule leads into a
foreign setup).

Open points that decide it:
- **Stage 1b (RouterOS trial): done** – the MikroTik (CHR 7.24.6) does VRF with WireGuard; firewall rules work
  with `in-interface=vrf-sN` ([routeros-spike.md](routeros-spike.md)). The platform argument for PBR is gone.
- **E21 (scale):** how both modes behave with 50 setups and 200 contexts.
