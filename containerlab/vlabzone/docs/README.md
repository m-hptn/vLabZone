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

**Viewing a lab:** open `topology/vlabzone-vrf.clab.yml` or `vlabzone-pbr.clab.yml` with the containerlab
VS Code extension. `topology/gen.py` writes a full layout for it next to each topology
(`*.clab.yml.annotations.json`): corporate side on the left, edge, aggregation switch and management in the
middle, one group per system switch (with its current setups) on the right, icons per node type.
`make topology` and `make up` always rewrite it, so nodes moved by hand are put back; the viewer's style
settings are kept. The layout files are local and not committed.

| Document | Content |
|---|---|
| [lab-vrf.md](lab-vrf.md) | VRF lab: overview, addresses, one request hop by hop, the VRF edge, switch, what carries over |
| [lab-pbr.md](lab-pbr.md) | PBR lab: overview, addresses, one request hop by hop, the PBR edge, setup gateways, switch, what carries over |
| [common.md](common.md) | nodes that are identical in both labs: corporate firewall, workspaces, broker, switches (aggregation + system switches, inventory and grouping), devices, management, settings for all nodes |
| [vrf-vs-pbr.md](vrf-vs-pbr.md) | comparison of the two modes: test results (E10), advantages, recommendation |

Concept and experiments: [`idea.md`](../../../idea.md) (German). Roadmap: [`PLAN.md`](../../../PLAN.md).

## Conventions

Every configuration item is tagged:

- **[real]**: part of the architecture. It carries over to the real environment (MikroTik edge, lab switches,
  corporate firewall).
- **[sim]**: only exists because the environment is simulated in containers.

Every command output in these documents was taken from the running labs, and every "Inspect" command was
run against them.
