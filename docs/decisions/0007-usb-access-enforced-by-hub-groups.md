# 0007 – USB access enforced by the hub's groups

- **Status:** Accepted for the lab – to verify on the real AnywhereUSB (client IDs)
- **Date:** 2026-11-21
- **Scope:** both

## Context
The AnywhereUSB has **one** TCP port (18574) for all 24 USB ports, so the edge can't give a setup only some of them.

## Options
| Option | For | Against |
|---|---|---|
| **Hub groups**: group N = setup N's USB ports, members = the clients of setup N's bookings | matches the hub's own model; revoke ends a running session | the broker must keep the hub in step (add on booking, remove on revoke) |
| One hub per setup | the edge alone could enforce it | hardware per setup; doesn't fit dynamic grouping |

## Evidence
- E26: without membership the hub lists nothing; own ports can be imported, foreign ones and non-members can't;
  revoking membership ends a running session within ~0.6 s ([consoles-usb.md](../../containerlab/vlabzone/docs/consoles-usb.md#3-usb-digi-anywhereusb-24-plus-e26)).
- Lab stand-in speaks USB/IP and uses the tunnel address as client ID **[sim]**; the real protocol is proprietary.

## Decision
USB access = tunnel (edge) **and** group membership (hub).

## Consequences
- Broker duty (stage 3): manage hub groups and client IDs per booking; check them in the reconcile loop.
- To verify on the real hub: how client IDs are assigned and whether group changes cut running sessions.
- Kernel `usbip attach` is tested only on hosts with `vhci-hcd` loaded.
