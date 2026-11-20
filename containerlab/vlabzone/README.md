# vLabZone – virtual test environment for hardware-pool access

A containerlab-based lab that reproduces the access path
**workspace → corporate firewall → WireGuard lab edge → VLAN switch → setups with overlapping networks**
on a single Linux host. Background and experiments: [`../../idea.md`](../../idea.md) (German),
implementation roadmap: [`../../PLAN.md`](../../PLAN.md).

> **Status:** two labs, `vlabzone-vrf` and `vlabzone-pbr`, one per edge mode, generated from
> `config/setups.yaml`. Both pass E01–E06 and E09; E10 compares the modes. Next: RouterOS trial (stage 1b).

---

## 1. Requirements

| | Minimum (`base`) | Scale tests (`scale`) |
|---|---|---|
| OS | Debian 12/13 or Ubuntu 24.04 (x86_64; arm64 best effort) | same |
| CPU / RAM / disk | 4 vCPU / 8 GB / 20 GB | 8 vCPU / 16 GB / 20 GB |
| Kernel | ≥ 5.6 with `wireguard`, `vrf`, `8021q`, `bridge` | same |
| RouterOS edge (optional) | `/dev/kvm` – on VMs: nested virtualization enabled | same |

- Bare metal or a VM on any hypervisor. **Not** Docker Desktop (macOS/Windows): its VM kernel lacks the modules.
- Cloud kernels (e.g. `linux-image-cloud-amd64`) sometimes lack `vrf` – the preflight tells you.
- The lab runs privileged containers. Use a dedicated host or VM.

On the **control node** (the machine you run Ansible from – it can be the lab host itself):

```bash
sudo apt install ansible rsync          # ansible-core >= 2.15
cd containerlab/vlabzone/deploy/ansible
ansible-galaxy collection install -r requirements.yml
```

---

## 2. Deploy on this machine (local)

```bash
cd containerlab/vlabzone/deploy/ansible
ansible-playbook site.yml -K            # -K asks for your sudo password
```

The default inventory (`inventory/localhost.yml`) uses the checkout in place. The playbook:

1. **preflight** – checks OS, kernel, modules, CPU/RAM/disk, KVM (`scripts/preflight.sh`)
2. **host_prereqs** – loads the kernel modules (now and at boot), installs Docker CE *only if docker is missing*,
   adds you to the `docker` group, installs the pinned containerlab release (checksum-verified)
3. **vlabzone** – renders `.env`, creates the test venv, builds the images `hwpool/<role>:<version>`
4. **routeros_image** – only if `vlab_routeros_enabled: true`: builds the MikroTik CHR image
5. **lab_deploy** – only with `--tags deploy`: host check + smoke lab

Then **log out and in again** (or run `newgrp docker`) so the `docker` group membership applies, and check:

```bash
cd containerlab/vlabzone
make host-check     # all PASS
make smoke          # 2-node lab: VLAN + VRF + WireGuard, then destroyed again (asks for sudo)
make test           # stage-0 tests
```

Provision and run the smoke test in one go: `ansible-playbook site.yml -K --tags all,deploy`.

---

## 3. Deploy on a remote machine (via SSH)

1. Make sure you can log in with an SSH key and the user has sudo rights:
   ```bash
   ssh-copy-id labadmin@192.0.2.10
   ```
2. Create your inventory:
   ```bash
   cd containerlab/vlabzone/deploy/ansible
   cp inventory/hosts.example.yml inventory/hosts.yml
   $EDITOR inventory/hosts.yml        # ansible_host, ansible_user, per-host options
   ```
3. Run the playbook:
   ```bash
   ansible-playbook -i inventory/hosts.yml site.yml -K                   # provision
   ansible-playbook -i inventory/hosts.yml site.yml -K --tags all,deploy # + smoke test
   ansible-playbook -i inventory/hosts.yml site.yml -K --limit lab-vm-02 # one host only
   ```
4. Work on the host:
   ```bash
   ssh labadmin@192.0.2.10
   cd ~/vLabZone/containerlab/vlabzone && make help
   ```

How the code gets to the remote host (`vlab_source`):

| Value | Behaviour |
|---|---|
| `sync` (default for remote) | rsync of your local checkout to `vlab_dir` (default `~/vLabZone`). Local changes are deployed without a commit. `.env`, `results/`, `.venv` on the target are kept. |
| `git` | `git clone` of `vlab_repo_url` at `vlab_repo_version` – once the repo has a remote. |
| `local` | Use the checkout in place – only for `ansible_connection: local`. |

Re-running the playbook is safe: it only changes what differs and rebuilds images only if they are
missing, the code changed, or `vlab_rebuild_images=true`.

---

## 4. Configuration

All defaults and pinned versions live in [`deploy/ansible/group_vars/all.yml`](deploy/ansible/group_vars/all.yml).
Override them per host in the inventory or in `deploy/ansible/host_vars/<host>.yml`, or ad hoc with `-e`.

| Variable | Default | Meaning |
|---|---|---|
| `vlab_profile` | `base` | `base` or `scale` – resource thresholds of the preflight |
| `vlab_edge_impl` | `linux` | `linux` or `routeros` (MikroTik CHR) |
| `vlab_edge_mode` | `vrf` | `vrf` or `pbr` |
| `vlab_routeros_enabled` | `false` | build the RouterOS CHR image (needs `/dev/kvm`) |
| `vlab_routeros_version` / `_sha256` | `7.24.6` | **set to the version of the real MikroTik**; sha256 of `chr-<v>.vmdk.zip` |
| `vlab_manage_docker` | `true` | install Docker CE if missing (an existing install is never changed) |
| `vlab_dir` | `~/vLabZone` | target directory for `sync`/`git` |
| `vlab_apt_mirror`, `vlab_http_proxy`, `vlab_https_proxy` | empty | for restricted networks (used in image builds) |
| `vlab_containerlab_version` | `0.82.0` | pinned containerlab release + checksums |

On the host the settings end up in `containerlab/vlabzone/.env` (rendered by Ansible; without Ansible
copy `.env.example`). `make` reads it, and any value can be overridden per call,
e.g. `make test EDGE_MODE=pbr`.

Example `host_vars/lab-vm-02.yml` for a host with the MikroTik edge:

```yaml
vlab_routeros_enabled: true
vlab_edge_impl: routeros
vlab_routeros_version: "7.24.6"
vlab_routeros_sha256: 30ee295fa1807c3de9819c7308e6b1629cf4bda29e7a42ea27788af75f63fdaf
```

### MikroTik / RouterOS CHR

`images/routeros/build.sh` downloads the official CHR image from download.mikrotik.com (checksum-verified),
clones [vrnetlab](https://github.com/srl-labs/vrnetlab) at a pinned commit and builds
`vrnetlab/mikrotik_routeros:<version>` for the containerlab kind `mikrotik_ros`. The download and build happen
in a cache directory, so nothing proprietary ends up in git. Run it by hand with `make build-routeros`.

The free CHR license limits every interface to 1 Mbit/s. That is enough for all functional tests, but
not for throughput experiments (E15, E17, E21, E22).

---

## 5. Daily use

```text
make help            overview of all targets
make host-check      preflight after provisioning
make build           (re)build the images
make build-routeros  build the RouterOS CHR image
make smoke           2-node smoke lab (VLAN, VRF, WireGuard)
make up | down | reset | inspect     lab of EDGE_MODE (default vrf), e.g. make up EDGE_MODE=pbr
make up-all | down-all | test-all    both labs (they can run side by side)
make test            pytest suite against the lab of EDGE_MODE
make test-stage1     only the experiments of one stage
make topology        regenerate topology/vlabzone-*.clab.yml after editing config/setups.yaml
make pcap NODE=corp-fw IF=eth10 SECONDS=30   capture into results/
make clean           remove venv and local results
```

containerlab is installed setuid: members of the `clab_admins` group (added by Ansible) run it
without sudo; everyone else gets `sudo containerlab`. Everything else only needs the `docker` group.

### Try it by hand

```bash
make up                                         # VRF lab, ~20 s, waits until all nodes are ready
make up EDGE_MODE=pbr                           # PBR lab (can run at the same time)
docker exec -it clab-vlabzone-vrf-ws-std bash   # a workspace
docker exec clab-vlabzone-vrf-lab-edge edge-ctl status
```

A tunnel from `ws-std` to setup 12 (what `tests/lib/wg.py` does for the tests – from stage 3 the
broker and `lab-agent` take over):

```bash
L=vrf                                               # or pbr – the commands are the same
E() { docker exec clab-vlabzone-$L-lab-edge "$@"; }  # works in bash and zsh
W() { docker exec clab-vlabzone-$L-ws-std "$@"; }
PUB=$(W sh -c 'umask 077; wg genkey > /run/vlab/k && wg pubkey < /run/vlab/k')
E edge-ctl peer add --setup 12 --pubkey "$PUB" --ip 10.250.1.12
W sh -c "ip link add wg-s12 type wireguard &&
  wg set wg-s12 private-key /run/vlab/k peer $(E edge-ctl pubkey --setup 12) \
     endpoint 10.20.99.2:$(E edge-ctl port --setup 12) allowed-ips 0.0.0.0/0 &&
  ip addr add 10.250.1.12/32 dev wg-s12 && ip link set wg-s12 up &&
  ip route add 192.168.1.0/24 dev wg-s12 src 10.250.1.12"
W curl -s http://192.168.1.20    # {"switch": "sys1", "device": "sys1-dev1", ..., "peer": "192.168.1.254"}

# clean up
E edge-ctl peer del --setup 12 --pubkey "$PUB"; W ip link del wg-s12
```

### Lab at a glance

Every node's configuration and why it is needed, per lab: [`docs/`](docs/README.md) – [VRF lab](docs/lab-vrf.md), [PBR lab](docs/lab-pbr.md), [common nodes](docs/common.md), [VRF vs. PBR](docs/vrf-vs-pbr.md).


| Node | Addresses | Purpose |
|---|---|---|
| `ws-std`, `ws-power`, `ws-ci` | 10.20.10.11–13 | workspaces (corporate) |
| `corp-svc` | 10.20.20.10 | corporate service |
| `corp-fw` | 10.20.10.1, .20.1, .99.1, .98.1 | corporate firewall, only UDP 51800–52099 / TCP 443, 8443 to the lab |
| `lab-edge` | 10.20.99.2, 10.198.0.2, 192.168.1.254 per setup | WireGuard + VRF per setup, `edge-ctl` |
| `broker` | 10.20.98.2, 10.198.0.1 | health endpoint on 8443 (API: stage 3) |
| `lab-agg` | 10.198.0.3 | aggregation switch: edge, system switches, (PBR) gateways |
| `lab-sys1`, `lab-sys2` | 10.198.0.4 / .5 | system switches, `sw-port <port> <vlan\|none>` regroups devices |
| `gw-12`, `gw-7`, `gw-5` (PBR lab only) | 10.199.0.12 / .7 / .5, 192.168.1.254 | setup gateways: MASQUERADE, `gw-ctl nat on\|off` |
| `sys1-dev1..4`, `sys2-dev1..4` | 192.168.1.20–22 (same IPs on purpose) | devices: HTTP identity, SSH `lab`/`lab`, syslog, iperf3. Setup 12 = sys1-dev1+2, setup 7 = sys1-dev3+4, setup 5 = sys2-dev1+2 (`config/setups.yaml`) |

---

## 6. Layout

```text
containerlab/vlabzone/
├── Makefile, VERSION, .env.example, requirements.txt, pytest.ini
├── deploy/ansible/        site.yml, inventories, group_vars, roles/{preflight,host_prereqs,vlabzone,routeros_image,lab_deploy}
├── scripts/               preflight.sh, smoke.sh, wait-ready.sh
├── topology/              gen.py → vlabzone-vrf.clab.yml, vlabzone-pbr.clab.yml (generated); smoke.clab.yml
├── images/                base + role images (router, edge, workspace, device, serial, mgmt, broker), routeros/build.sh
├── config/                setups.yaml (setups, VLANs, devices), corp-fw.nft, users.yaml
├── docs/                  per-lab documentation: lab-vrf.md, lab-pbr.md, common.md, vrf-vs-pbr.md
├── tests/                 pytest suite, one module per experiment
└── results/               experiment protocols and pcaps (not committed)
```

---

## 7. Troubleshooting

| Symptom | Fix |
|---|---|
| `permission denied ... docker.sock` after provisioning | log out and in again (new `docker` group membership) |
| preflight: `kernel module vrf missing` | install the generic kernel (`linux-image-amd64` / `linux-generic`) and reboot |
| preflight: `/dev/kvm missing` with RouterOS enabled | enable VT-x/AMD-V in the BIOS, or nested virtualization on the hypervisor (vSphere: "Expose hardware assisted virtualization"; Proxmox: CPU type `host`) |
| deploy: `Pool overlaps with other one` | set `VLAB_MGMT_SUBNET` / `vlab_mgmt_subnet` to a free range (`make host-check` shows conflicts) |
| `make up` hangs at wait-ready | the log of the node that isn't ready is printed; `docker logs clab-vlabzone-<mode>-<node>` |
| smoke: `WARN: eth0 exists in node` | the containerlab version ignores `network-mode: none` – the nft fallback (idea.md §4.4) is needed |
| image build fails behind a proxy | set `vlab_http_proxy` / `vlab_https_proxy` or `vlab_apt_mirror` |
