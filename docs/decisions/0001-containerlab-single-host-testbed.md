# 0001 – Single-host containerlab test bed

- **Status:** Accepted
- **Date:** 2026-11-20
- **Scope:** lab only

## Context
The access architecture (workspaces → corporate firewall → WireGuard lab edge → switches → setups with overlapping
networks) must be tried before vSphere, Proxmox, the corporate network and the lab switches are touched
([concept §1](../concept.md#1-purpose-and-goals)). It must be reproducible on any team member's Linux host or VM.

## Options
| Option | For | Against |
|---|---|---|
| **containerlab**, all nodes `kind: linux`, `network-mode: none` | point-to-point veth links bypass Docker IPAM, so overlapping subnets work; no management bypass | needs root for deploy; privileged containers |
| Docker Compose | known to everyone | Docker refuses overlapping subnets; re-addressing in entrypoints; filters "foreign" addresses |
| Nested VMs (Proxmox, real RouterOS hardware) | closest to reality | slow to build and reset, not one command, needs a big host |

## Evidence
- `network-mode: none` works with containerlab 0.82: no node has `eth0`, E05 finds no bypass ([history](../history.md), stage 1).
- Six labs (three edge modes × Linux/MikroTik edge) run side by side on 8 vCPU / 15 GB and pass their suites
  ([experiments.md](../experiments.md)).
- Provisioning with Ansible on bare metal or a VM: [lab README](../../containerlab/vlabzone/README.md).

## Decision
containerlab on one Linux host, images built locally, provisioned with Ansible. The MikroTik edge runs as RouterOS
CHR inside containerlab (vrnetlab, needs KVM).

## Consequences
- What the lab can't prove (Proxmox SDN, real passthrough, real switches, Windows clients): [concept §9](../concept.md#9-limits-and-follow-up-stage).
- Every node configuration is tagged **[real]** (carries over) or **[sim]** (only because it is simulated).
