# Decisions (ADRs)

Architecture decision records: one short file per question the lab helps answer. Each names the options, links the
**evidence** (experiments, detail docs with real outputs) and states the decision – or, while it is open, the working
assumption the lab uses.

| No. | Decision | Status | Date |
|---|---|---|---|
| [0001](0001-containerlab-single-host-testbed.md) | Single-host containerlab test bed | Accepted | 2026-11-20 |
| [0002](0002-tunnel-addresses-setup-first.md) | Tunnel addresses 10.250.\<setup\>.\<user\> | Accepted | 2026-11-20 |
| [0003](0003-one-corporate-port-edge-mode.md) | Edge mode: VRF, PBR or hybrid | **Proposed** (hybrid) | 2026-11-20 |
| [0004](0004-routeros-edge-driver-rest.md) | MikroTik edge driven over the RouterOS REST API | Accepted | 2026-11-20 |
| [0005](0005-appliance-placement.md) | Network placement of the shared serial/USB appliances | **Open** | 2026-11-21 |
| [0006](0006-nport-operation-mode.md) | Moxa NPort operation mode: RFC 2217 or Real COM | **Open** | 2026-11-21 |
| [0007](0007-usb-access-enforced-by-hub-groups.md) | USB access enforced by the hub's groups | Accepted (lab) | 2026-11-21 |

## Statuses

| Status | Meaning |
|---|---|
| **Open** | question known, not decided; the lab runs on a stated working assumption |
| **Proposed** | the lab has evidence and a recommendation; waiting for the team |
| **Accepted** | decided; changing it needs a new ADR that supersedes this one |
| **Superseded** | replaced by a later ADR (linked) |

## Writing one

1. Copy [0000-template.md](0000-template.md) to the next free number.
2. Evidence first: run the experiment in the relevant labs ([evaluating.md](../evaluating.md)) and link the results.
3. Add a row above and open a merge request; the team discussion happens there.
