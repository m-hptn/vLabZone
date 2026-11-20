# vLabZone documentation

What each node of the labs is configured to do, and **why**.

There are two labs. Both are generated from the same source ([`config/setups.yaml`](../config/setups.yaml),
[`topology/gen.py`](../topology/gen.py)) and differ only in how the lab edge separates the setups. Both model the real switching: an aggregation switch
with the edge, and system switches whose devices are grouped dynamically into setups.

| Lab | Edge mode | Containers | Start | Document |
|---|---|---|---|---|
| `vlabzone-vrf` | one VRF per setup | `clab-vlabzone-vrf-<node>` | `make up` | [lab-vrf.md](lab-vrf.md) |
| `vlabzone-pbr` | one `wg0` + policy routing, a gateway per setup | `clab-vlabzone-pbr-<node>` | `make up EDGE_MODE=pbr` | [lab-pbr.md](lab-pbr.md) |

Both labs can run at the same time (`make up-all`).

## Viewing a lab

Open one of these files with the containerlab VS Code extension (TopoViewer). Each shows the same lab with
a different layout:

| View | File (`vrf` or `pbr`) | Shows |
|---|---|---|
| **Network** | `topology/vlabzone-vrf.clab.yml` | the switching: corporate → edge → aggregation switch → system switches → devices, one group per system switch with its current setups |
| **Deployment** | `topology/views/vlabzone-vrf.deployment.clab.yml` | which real hardware/platform runs which node: vSphere (workspaces), corporate network, MikroTik (edge), Proxmox cluster (broker, PBR gateways, later access instances), Lancom aggregation switch, one rack per system switch |
| **Logical** | `topology/views/vlabzone-vrf.logical.clab.yml` | the access path left to right: workspaces → corporate firewall → edge → **gateway** → setup. One lane per setup; in the PBR lab `gw-N` sits in the lane, in the VRF lab a label names the edge VRF that acts as gateway. Switches are shown separately as "transport", ungrouped devices in their own group |

- The files in `topology/views/` are generated copies of the topology with the same lab name: **view only,
  don't deploy them**, use `make up`. (Tested with containerlab 0.82: deploying a view while the lab runs
  changes no node, but creates a stray lab directory under `topology/views/`; with the lab down it would deploy
  the same lab from there.) The extension's lab list shows them as extra entries.
- `topology/gen.py` writes the full layout for every view (`*.clab.yml.annotations.json` next to each file).
  `make topology` and `make up` always rewrite it, so nodes moved by hand are put back; the viewer's style
  settings are kept. The layout files are local and not committed.
- A test (`test_viewer_layout_complete_and_without_overlaps`) checks that every node appears in every view and
  that no node or group overlaps another, computed the way the viewer sizes groups.

| Document | Content |
|---|---|
| [lab-vrf.md](lab-vrf.md) | VRF lab: overview, addresses, one request hop by hop, the VRF edge, switch, what carries over |
| [lab-pbr.md](lab-pbr.md) | PBR lab: overview, addresses, one request hop by hop, the PBR edge, setup gateways, switch, what carries over |
| [common.md](common.md) | nodes that are identical in both labs: corporate firewall, workspaces, broker, switches (aggregation + system switches, inventory and grouping), devices, management, settings for all nodes |
| [vrf-vs-pbr.md](vrf-vs-pbr.md) | comparison of the two modes: test results (E10), advantages, recommendation |
| [routeros-spike.md](routeros-spike.md) | stage 1b: MikroTik CHR as VRF edge – results, RouterOS configuration, firewall findings |

Concept and experiments: [`idea.md`](../../../idea.md) (German). Roadmap: [`PLAN.md`](../../../PLAN.md).

## Conventions

Every configuration item is tagged:

- **[real]**: part of the architecture. It carries over to the real environment (MikroTik edge, lab switches,
  corporate firewall).
- **[sim]**: only exists because the environment is simulated in containers.

Every command output in these documents was taken from the running labs, and every "Inspect" command was
run against them.
