# 0004 – MikroTik edge driven over the RouterOS REST API

- **Status:** Accepted
- **Date:** 2026-11-20
- **Scope:** both

## Context
The real lab edge is a MikroTik. The lab must show that every edge mode works on RouterOS, and the broker needs a
driver that can later run against the real router.

## Options
| Option | For | Against |
|---|---|---|
| `.rsc` startup templates rendered from `setups.yaml` (original plan) | readable export | only the initial state; bookings still need an API |
| **REST driver** (`drivers/edge_routeros.py`) configures the router and implements the `edge-ctl` commands | one code path for setup, booking and reconcile; the broker reuses it | RouterOS applies some changes asynchronously (driver waits) |

## Evidence
- E27: E10's misconfigurations repeated over REST give the same results as on Linux; full suites pass in all three
  modes ([lab-routeros.md](../../containerlab/vlabzone/docs/lab-routeros.md), [routeros-spike.md](../../containerlab/vlabzone/docs/routeros-spike.md)).
- Findings that matter for the real router: firewall rules inside a VRF must name the VRF; new rules take ~3 s;
  VRF tables appear asynchronously; stock RouterOS exposes management on every main address.

## Decision
The REST driver is the MikroTik edge implementation (`EDGE_IMPL=routeros`), with the same commands as `edge-ctl`.

## Consequences
- Tested with CHR 7.24.6; **repeat with the real router's RouterOS version** (`vlab_routeros_version`).
- Free CHR licence caps interfaces at 1 Mbit/s: throughput experiments (E15, E17, E21, E22) need a licence.
