# Contributing

vLabZone is a shared playground and evaluation tool. Anyone can try an idea in a branch. What lands on `main` stays
reproducible: every lab still builds, and every experiment still passes or skips with a reason.

## Workflow

1. **Branch** from `main`, one topic per branch (`eval/appliances-in-mgmt-vlan`, `stage3/broker-api`, `docs/…`).
2. **Change** the code, the config or the docs (see the conventions below).
3. **Test** in every lab the change affects. When in doubt, test all of them:
   ```bash
   make up-all && make test-all                                   # Linux edge: vrf, pbr, hybrid
   make up-all EDGE_IMPL=routeros && make test-all EDGE_IMPL=routeros   # MikroTik edge (needs KVM)
   ```
   A change that only touches documentation needs no lab run, but its commands and outputs must still be true (see
   below).
4. **Merge request** against `main`, with the test totals per lab in the description (e.g. `hybrid 82 passed,
   11 skipped`). An evaluation also carries its ADR ([docs/decisions/](docs/decisions/README.md)), usually as
   **Proposed**, and the team discusses it in the merge request.

Experiments are allowed to fail on a branch: a variant that fails E02 is a result. Write it down in the ADR and keep
it off `main`, or merge it behind a setting that is off by default.

## Conventions

### Language and commits
- English everywhere: code, comments, docs, commits. ([docs/concept.de.md](docs/concept.de.md) is the frozen German
  original.)
- Commit subject: what changed, with the stage/step or experiment when there is one, e.g.
  `Stage 2 step 13: serial consoles (NPort) and USB (AnywhereUSB) as shared appliances (E14, E26)` or
  `Docs: …`. The body explains why and lists the test totals.

### Configuration
- [`config/setups.yaml`](containerlab/vlabzone/config/setups.yaml) is the single source for inventory, grouping,
  edge networks and appliances. Scripts read it through `vlab-cfg`, tests through `tests/lib/labcfg.py`. Don't
  repeat its values elsewhere. Known exceptions are listed in [evaluating.md §4](docs/evaluating.md#4-address-layout).
- After editing it: `make topology` (the generated topologies and view files are committed; the layout files are
  not).
- Pinned versions live in `deploy/ansible/group_vars/all.yml`. No host-specific paths, users or hostnames in the
  code.
- No secrets in git: CHR licences and other keys are Ansible vault variables (`vault*.yml` is ignored).

### Experiments
- One pytest module per experiment: `tests/test_eNN_<topic>.py` with `pytestmark = pytest.mark.stageN`.
- Every case writes a protocol with `write_protocol(...)` before it asserts, so failures are recorded too.
- Something that doesn't apply to a lab is skipped with a reason, never silently passed.
- New experiment → a row in [docs/experiments.md](docs/experiments.md). Step by step:
  [evaluating.md §3](docs/evaluating.md#add-an-experiment).

### Documentation
- **Project level** ([docs/](docs/)): concept, decisions, experiments, roadmap, glossary, guides.
- **Lab level** ([containerlab/vlabzone/docs/](containerlab/vlabzone/docs/README.md)): every node and setting of the
  labs. Each setting is tagged **[real]** (carries over to the real environment) or **[sim]** (exists only because
  the lab is simulated), and says **why** it is there.
- Every command output in the docs is copied from a running lab, and every "inspect" command has been run. If the lab
  changes, update the output or remove it.
- A finding (something that surprised us and matters for the real environment) goes into the topic's doc with the
  evidence, into the "Carried over to the real environment" section, and into an ADR if it changes a decision.
- Relative links only, so the docs read the same in an editor, on the Git server and offline.

## Where to change what

| I want to … | Look at |
|---|---|
| change devices, setups, VLANs, networks | `containerlab/vlabzone/config/setups.yaml`, then [evaluating.md §3](docs/evaluating.md#change-the-inventory-or-the-grouping) |
| change how the edge works | `containerlab/vlabzone/images/edge/` (Linux), `containerlab/vlabzone/drivers/edge_routeros.py` (MikroTik) |
| add an edge mode | [evaluating.md §3](docs/evaluating.md#add-an-edge-mode) |
| change the topology or the views | `containerlab/vlabzone/topology/gen.py` |
| change the corporate side | `containerlab/vlabzone/images/router/roles/corp-fw.sh`, `config/corp-fw.nft` |
| change what a workspace can do | `containerlab/vlabzone/images/workspace/lab` |
| change provisioning | `containerlab/vlabzone/deploy/ansible/`, `scripts/preflight.sh` |
| record a decision | [docs/decisions/](docs/decisions/README.md) |
