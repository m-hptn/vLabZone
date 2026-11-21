# 0003 – Edge mode: VRF, PBR or hybrid

- **Status:** Proposed – recommendation **hybrid**, to be confirmed by the team and by E21 (scale)
- **Date:** 2026-11-20
- **Scope:** both

## Context
The edge has to keep setups with identical networks (`192.168.1.0/24` everywhere) apart. The deciding constraint:
the **corporate firewall should need one rule with one port**.

## Options
| Option | For | Against |
|---|---|---|
| VRF per setup | per-booking mistakes fail closed by construction; an interface restart affects one setup | one WireGuard port per setup → the corporate rule is a port range |
| PBR (one `wg0`, gateway per setup) | one port; NAT on the gateways | a rule per booking; a wrong one gives two-way access to a foreign setup; shared transfer VLAN |
| **hybrid** (one `wg0`, setups as edge VRFs) | one port; one change per booking; no gateways; wrong setup rule fails closed | `wg0` restart interrupts everyone; edge services need an input filter |

## Evidence
- Comparison, misconfiguration results (E10) and RouterOS behaviour: [vrf-vs-pbr.md](../../containerlab/vlabzone/docs/vrf-vs-pbr.md).
- All three modes pass the same suite on the Linux edge and on the MikroTik ([experiments.md](../experiments.md)).
- Labs: [VRF](../../containerlab/vlabzone/docs/lab-vrf.md), [PBR](../../containerlab/vlabzone/docs/lab-pbr.md), [hybrid](../../containerlab/vlabzone/docs/lab-hybrid.md).

## Decision
Proposed: **hybrid** with the tunnel addresses of [0002](0002-tunnel-addresses-setup-first.md). Without the one-port
constraint VRF is equally safe and simpler.

## Consequences
- Open evidence: E21 (50 setups / 200 contexts per mode) and a repeat on the real MikroTik's RouterOS version.
- The broker's reconcile loop must compare peers with bookings (the one thing a booking can still get wrong).
