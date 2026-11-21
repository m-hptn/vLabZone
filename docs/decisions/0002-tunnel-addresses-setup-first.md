# 0002 – Tunnel addresses 10.250.\<setup\>.\<user\>

- **Status:** Accepted
- **Date:** 2026-11-20
- **Scope:** both

## Context
The concept used `10.250.<user>.<setup>` ([concept §4.3](../concept.md#43-address-plan-of-the-test-environment)).
With one WireGuard port (see [0003](0003-one-corporate-port-edge-mode.md)) the edge must tell setups apart by the
tunnel address. With the setup in the last octet that needs a rule per booking, and RouterOS can't match the last
octet of an address at all.

## Options
| Option | For | Against |
|---|---|---|
| `10.250.<user>.<setup>` | user-centric | a rule per booking; not expressible on RouterOS |
| **`10.250.<setup>.<user>`** | every setup is a /24 prefix: one static rule and one forward rule per setup; works on Linux and RouterOS | max. 255 setups, 254 users per setup within one /16 |

## Evidence
- Full reasoning, limits and where it is used: [tunnel-addresses.md](../../containerlab/vlabzone/docs/tunnel-addresses.md).
- E10: a wrong setup rule fails closed in hybrid mode; `edge-ctl` refuses addresses outside the setup's prefix
  (E10-address-check, all modes). E27 repeats this on RouterOS.
- Side benefit used later: the shared appliances see the setup in the source address (no NAT,
  [0005](0005-appliance-placement.md)).

## Decision
Tunnel address = `tunnel_net` + setup × 256 + user (`10.250.<setup>.<user>`), computed by `vlab-cfg setup-prefix`
/ `tunnel-ip`.

## Consequences
- The broker allocates user numbers per setup (1–254).
- More than 255 setups or 254 concurrent users per setup need a bigger `tunnel_net` and a different split; the
  helpers in `vlab-cfg` are the only place that computes it.
