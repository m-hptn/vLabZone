# Evaluating a variant

How to use the lab to answer an architecture question: change one thing, run the experiments in the labs it affects,
compare the protocols, and record the outcome as a [decision](decisions/README.md).

Paths below are relative to `containerlab/vlabzone/` unless they start with `docs/`.

## 1. What can be varied

| Knob | Where | Takes effect with | Experiments that tell you something |
|---|---|---|---|
| Edge mode (VRF / PBR / hybrid) | `EDGE_MODE=` on every `make` call, or `.env` | `make up EDGE_MODE=…` | all; E10 for the differences |
| Edge implementation (Linux / MikroTik) | `EDGE_IMPL=linux\|routeros` | `make up EDGE_IMPL=routeros` (needs KVM and `make build-routeros`) | all; E27 for parity |
| Switch inventory: system switches, ports, devices, device IPs | `config/setups.yaml` → `switches:` | `make topology && make reset` | E02, E05, E20 |
| Grouping: which devices form which setup, setup VLAN, setup network and gateway | `config/setups.yaml` → `setups:` | `make topology && make reset` | E02, E03, E04, E20 |
| Tunnel, alias, PBR transfer networks; WireGuard ports | `config/setups.yaml` → `edge:` | `make reset` | E01, E06, E10, E13 |
| Shared appliances: VLAN, network, NPort mode and ports, USB hub | `config/setups.yaml` → `appliances:` and per device `serial:` / `usb:` | `make topology && make reset` | E14, E26 |
| Corporate allowance | `config/corp-fw.nft` (port set per mode in `images/router/roles/corp-fw.sh`) | `make reset` | E01, E05 |
| Device default gateway (wrong on purpose) | `config/setups.yaml` → `device_default_gateway` | `make reset` | E04 |

`config/setups.yaml` is the single source for everything it contains: the topology generator, the edge, the
switches, the PBR gateways, the appliances and the test suite all read it through
[`vlab-cfg`](../containerlab/vlabzone/images/base/rootfs/usr/local/bin/vlab-cfg). What is **not** in it yet is listed
in [§4](#4-address-layout).

## 2. Run and compare

```bash
cd containerlab/vlabzone
make up-all                                    # vrf, pbr, hybrid side by side (Linux edge)
make test-all                                  # the suite against each of them
make up-all test-all EDGE_IMPL=routeros        # the same with the MikroTik edge (~4 min per lab)
```

Each test case writes `results/<ID>-<impl>-<mode>-<timestamp>.json` with `result`, `measurements`, `observations` and
`consequence`. The newest protocol per case and lab, side by side:

```bash
python3 - <<'EOF'
import glob, json
latest = {}
for f in glob.glob("results/*.json"):
    d = json.load(open(f))
    k = (d["experiment"], d["edge"])
    if k not in latest or d["date"] > latest[k]["date"]:
        latest[k] = d
edges = sorted({e for _, e in latest})
print("case".ljust(28), *(e.ljust(16) for e in edges))
for exp in sorted({x for x, _ in latest}):
    print(exp.ljust(28), *((latest[(exp, e)]["result"] if (exp, e) in latest else "-").ljust(16) for e in edges))
EOF
```

For a variant of the same lab (e.g. a different grouping), run once before and once after the change; the timestamps
keep both. `make clean` removes all protocols.

Captures: `make pcap NODE=corp-fw IF=eth10 SECONDS=30` writes a pcap to `results/`; tests use `tests/lib/capture.py`.

## 3. Typical changes

### Change the inventory or the grouping

1. Edit `config/setups.yaml`. Rules: a setup's devices sit on **one** system switch; each setup has its own VLAN and
   id (1–255, see [ADR 0002](decisions/0002-tunnel-addresses-setup-first.md)); two devices with the same IP may sit
   on one switch but not in one setup (E20b).
2. `make topology` regenerates the topologies and the three views (network, deployment, logical).
3. `make reset EDGE_MODE=…` and `make test`. Tests that name devices (e.g. `sys1-dev1` in E14) assume the lab
   inventory; a different inventory may need them adjusted.

At runtime (no redeploy) a device can be regrouped as the broker will do it: `sw-port` on the system switch,
`edge-ctl console allow|deny` on the edge, `hub-ctl group set` on the USB hub (E20, E14e).

### Add an edge mode

The three modes show the pattern. A new mode touches:

| File | What |
|---|---|
| `images/edge/edge-<mode>.sh` and the `case` in `images/edge/entrypoint.sh` | the edge itself |
| `images/edge/edge-ctl` | what a booking changes (peer, rule) |
| `images/edge/edge-appliances.sh` | routing to the appliance VLAN |
| `images/base/rootfs/usr/local/bin/vlab-cfg` → `switch_config` | which VLANs the edge trunk carries |
| `images/router/roles/corp-fw.sh` | the corporate allowance for the mode |
| `topology/gen.py` → `MODES` and the mode branches; `Makefile` → `MODES` | the lab and its views |
| `drivers/edge_routeros.py` | the MikroTik equivalent |
| `tests/test_e10_vrf_vs_pbr.py` | the mode's misconfiguration cases |
| `containerlab/vlabzone/docs/lab-<mode>.md`, `vrf-vs-pbr.md` | the documentation |

### Add an experiment

1. `tests/test_eNN_<topic>.py` with `pytestmark = pytest.mark.stageN`. Use the fixtures in `tests/conftest.py`
   (`lab`, `tunnel`) and `tests/lib/labcfg.py` for anything from `setups.yaml`; don't hardcode addresses.
2. Every case calls `write_protocol(id, title, passed, measurements, observations=…, consequence=…)` from
   `tests/lib/results.py` **before** it asserts, so failures leave a protocol too.
3. Run it in all labs it applies to; skip with a reason (`pytest.skip`) where it doesn't apply.
4. Add a row to [experiments.md](experiments.md) and, if the result changes a decision, an ADR.

## 4. Address layout

Evaluating an address schema means changing addresses in one place and re-running the suite. Today that works for
the setups, tunnels, alias, transfer and appliance networks, but not for the corporate side and the lab management.

| Address range | Config-driven? | Where it is set |
|---|---|---|
| Setup networks, gateways, device IPs (`192.168.1.0/24`, `.254`) | **yes** | `config/setups.yaml` → `setups:`, `switches:` |
| Tunnel network `10.250.0.0/16`, per setup `10.250.<setup>.0/24` | **yes** (the /24-per-setup split is fixed: [ADR 0002](decisions/0002-tunnel-addresses-setup-first.md)) | `edge.tunnel_net` |
| Alias network `10.200.0.0/16` | **yes** | `edge.alias_net` |
| PBR transfer `10.199.0.0/24`, VLAN 199 | **yes** | `edge.transfer` |
| Appliance VLAN `10.197.0.0/24` and appliance IPs | **yes** | `appliances:` |
| Switch management IPs `10.198.0.3–5` | **yes** | `aggregation.mgmt_ip`, `switches[].mgmt_ip` |
| Edge and broker management `10.198.0.2`, `10.198.0.1` | no | `images/edge/entrypoint.sh`, `images/broker/entrypoint.sh`, `drivers/edge_routeros.py` |
| Corporate segments `10.20.10/24` (workspaces), `10.20.20/24` (service), `10.20.98/30`, `10.20.99/30` (transfers) | no | `images/router/roles/corp-fw.sh`, `images/edge/entrypoint.sh`, `images/broker/entrypoint.sh`, `images/workspace/entrypoint.sh`, `topology/gen.py` (node env), `drivers/edge_routeros.py` |
| Edge endpoint for the workspaces `10.20.99.2` | no | `tests/lib/wg.py` (`EDGE_ENDPOINT`) |
| containerlab's own management network | yes, per host | `VLAB_MGMT_SUBNET` in `.env` |

The test suite mostly reads addresses through `tests/lib/labcfg.py`. Literal addresses remain in some test modules,
mainly the setup network (`192.168.1.x`, e.g. E03, E04, E10, E20), the corporate side (E05, E09) and a few tunnel,
alias and transfer addresses (`tests/lib/wg.py`, E05, E10, E13); a different schema needs those adjusted too. A quick inventory:

```bash
cd containerlab/vlabzone
grep -rnE '10\.20\.[0-9]|10\.198\.0\.[12]\b' images drivers topology/gen.py tests | grep -v '^\S*:\s*#'
grep -rlE '192\.168\.1\.[0-9]' tests
```

**Backlog:** move the corporate segments and the edge/broker management addresses into a `corporate:` / `management:`
section of `setups.yaml`, read them in the entrypoints and the driver through `vlab-cfg`, and replace the literals in
the tests with `labcfg` lookups. Then an alternative schema is one file plus `make reset`.

## 5. Record the outcome

A comparison is finished when its result is written down where the team will find it:

- the detail doc of the topic (`containerlab/vlabzone/docs/…`) gets the real outputs and the result table;
- the catalogue row in [experiments.md](experiments.md) gets its status;
- the question gets an ADR in [decisions/](decisions/README.md) – **Proposed** with the evidence, **Accepted** once the
  team agrees.
