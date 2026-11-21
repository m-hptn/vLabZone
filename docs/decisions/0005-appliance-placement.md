# 0005 – Network placement of the shared serial/USB appliances

- **Status:** Open – the lab assumes an own appliance VLAN behind the edge
- **Date:** 2026-11-21
- **Scope:** real environment

## Context
The serial consoles hang on one Moxa NPort 5600, the USB interfaces on one Digi AnywhereUSB 24 Plus. Each serves the
devices of **all** setups, so neither can sit in a setup VLAN. Where they live in the real network isn't decided.

## Options
| Option | For | Against |
|---|---|---|
| **Own VLAN behind the lab edge, no NAT** (lab: VLAN 197, 10.197.0.0/24) | only the edge reaches them, and it knows the setup of every tunnel; appliances see `10.250.<setup>.<user>`; no new corporate rule | edge needs a route leak per setup VRF (and in VRF mode a reply route per setup) |
| In the lab management network | no new VLAN | mixes user traffic with switch management; the edge would still have to filter |
| Reachable from the corporate network | no tunnel needed | needs new corporate rules; setups can't be told apart |

## Evidence
- E14 and E26 pass in all six labs with the own VLAN ([consoles-usb.md](../../containerlab/vlabzone/docs/consoles-usb.md)).
- Finding (VRF mode): the reply route `10.250.N.0/24 dev wg-sN` is lost when `wg-sN` changes VRF or is recreated
  – the reconcile loop must check it. Hybrid and PBR are not affected.

## Decision
Open. Working assumption: own appliance VLAN behind the edge, without NAT.

## Consequences
If decided differently, `appliances:` in `config/setups.yaml` and `images/edge/edge-appliances.sh` /
`_appliances()` in the RouterOS driver change; E14/E26 show whether the separation still holds.
