# vLabZone – virtual test environment for hardware-pool access

A containerlab-based lab that reproduces the access path
**workspace → corporate firewall → WireGuard lab edge → VLAN switch → setups with overlapping networks**
on a single Linux host. Background and experiments: [`../../idea.md`](../../idea.md) (German),
implementation roadmap: [`../../PLAN.md`](../../PLAN.md).

> **Status: stage 0.** Host provisioning, images, preflight and a smoke lab are in place.
> The full topology (`topology/hwpool.clab.yml`) comes with stage 1.

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
make up | down | reset | inspect     full lab (from stage 1)
make test            pytest suite (EDGE_IMPL / EDGE_MODE respected)
make clean           remove venv and local results
```

containerlab needs root, so `make up/down/smoke` call `sudo containerlab`. Everything else only needs
the `docker` group.

---

## 6. Layout

```text
containerlab/vlabzone/
├── Makefile, VERSION, .env.example, requirements.txt, pytest.ini
├── deploy/ansible/        site.yml, inventories, group_vars, roles/{preflight,host_prereqs,vlabzone,routeros_image,lab_deploy}
├── scripts/               preflight.sh, smoke.sh
├── topology/              smoke.clab.yml (hwpool.clab.yml from stage 1)
├── images/                base + role images (router, edge, workspace, device, serial, mgmt, broker), routeros/build.sh
├── config/                setups.yaml (setups, VLANs, devices), users.yaml
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
| smoke: `WARN: eth0 exists in node` | the containerlab version ignores `network-mode: none` – the nft fallback (idea.md §4.4) is needed |
| image build fails behind a proxy | set `vlab_http_proxy` / `vlab_https_proxy` or `vlab_apt_mirror` |
