# vLabZone

A virtual test bed for **remote access to a hardware pool**: users in vSphere workspaces book a setup of lab devices
and reach it through one corporate firewall rule, a WireGuard lab edge and VLAN switches – even though every setup
uses the same network (`192.168.1.0/24`). The whole path runs in containers on one Linux host, built with containerlab,
and is checked by an automated experiment suite.

The project is three things for the team:

| | Use it to | Start with |
|---|---|---|
| **Idea** | understand the access architecture and why it looks like this | [concept](docs/concept.md), [decisions](docs/decisions/README.md), [glossary](docs/glossary.md) |
| **Playground** | start a lab, open a tunnel, break things, look at packets | [quickstart](#quickstart), [lab docs](containerlab/vlabzone/docs/README.md) |
| **Evaluation tool** | compare edge modes, Linux vs. MikroTik, address layouts, appliance placement – with tests as evidence | [evaluating a variant](docs/evaluating.md), [experiments](docs/experiments.md) |

## Architecture

```text
 corporate (simulated)                          │  lab zone
                                                │
 ws-std ──┐                                     │                 ┌── lab-sys1 ── sys1-dev1..4   setups 12, 7
 ws-power ├── corp-fw ══ one rule: WireGuard UDP ══ lab-edge ═══ lab-agg ── lab-sys2 ── sys2-dev1..4   setup 5
 ws-ci ───┘   (+ corp-svc)                      │  (Linux or      ├── nport1  serial consoles (Moxa NPort 5600)
                                                │   MikroTik)     ├── ausb1   USB (Digi AnywhereUSB 24 Plus)
                                                │  VRF | PBR |    └── gw-N    setup gateways (PBR mode only)
                                                │  hybrid         broker (stage 3), lab-mgmt
```

- A **setup** is a group of devices on one system switch with its own VLAN; setups overlap in addressing on purpose.
- The **edge** decides which setup a tunnel reaches. Three modes are built and compared: VRF, PBR and hybrid
  (recommended, [ADR 0003](docs/decisions/0003-one-corporate-port-edge-mode.md)), each with a Linux or a MikroTik
  (RouterOS CHR) edge.
- Real hardware layout: [docs/diagrams/device_arc.drawio](docs/diagrams/device_arc.drawio). Each lab also has a
  deployment and a logical view for the containerlab VS Code extension ([lab docs](containerlab/vlabzone/docs/README.md#viewing-a-lab)).

## Status

Stages 0–2 are done: six labs (VRF, PBR, hybrid × Linux, MikroTik) pass every implemented experiment (E01–E06, E09–E11,
E13, E14, E20, E26, E27). Next is stage 3, the broker and the `lab-agent`. Details: [roadmap](docs/roadmap.md),
[experiments](docs/experiments.md).

Open decisions: [appliance placement](docs/decisions/0005-appliance-placement.md),
[NPort mode](docs/decisions/0006-nport-operation-mode.md), [edge mode](docs/decisions/0003-one-corporate-port-edge-mode.md)
(proposed: hybrid).

## Quickstart

On a Debian 12/13 or Ubuntu 24.04 host or VM (not Docker Desktop) with 4 vCPU / 8 GB:

```bash
sudo apt install ansible rsync
cd containerlab/vlabzone/deploy/ansible
ansible-galaxy collection install -r requirements.yml
ansible-playbook site.yml -K              # kernel modules, Docker, containerlab, images; then log out and in
cd ../..                                  # containerlab/vlabzone
make up EDGE_MODE=hybrid                  # ~20 s
make test EDGE_MODE=hybrid                # the experiment suite
```

Remote hosts, the MikroTik edge, settings and troubleshooting: [lab README](containerlab/vlabzone/README.md).
Every `make` target also works from the repository root (`make help`).

## Repository map

```text
vLabZone/
├── README.md               this file
├── CONTRIBUTING.md         how we work on it: conventions, adding experiments, setups, edge modes
├── Makefile                runs any lab target from the root (make up == make -C containerlab/vlabzone up)
├── docs/                   project-level documentation
│   ├── concept.md          the concept and the experiment list (German original: concept.de.md)
│   ├── decisions/          architecture decision records (ADRs): open, proposed, accepted
│   ├── experiments.md      catalogue E00–E27 with status per lab
│   ├── evaluating.md       how to evaluate a variant; what is config-driven
│   ├── roadmap.md          status and next stages
│   ├── history.md          how each step was built, with its findings
│   ├── glossary.md         terms
│   └── diagrams/           real hardware layout (draw.io)
└── containerlab/vlabzone/  the lab
    ├── config/setups.yaml  single source: inventory, grouping into setups, edge and appliance networks
    ├── topology/           gen.py → one topology per mode (+ -ros variants) and view files
    ├── images/             container images: edge, router (corp-fw, switches, gateways), workspace, device, serial, usbhub, …
    ├── drivers/            MikroTik edge over the RouterOS REST API
    ├── tests/              one pytest module per experiment
    ├── deploy/ansible/     host provisioning
    ├── docs/               per-lab documentation: every node, every setting, why, with real outputs
    └── results/            experiment protocols and captures (local, not committed)
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Documentation, code and commits are in English.
