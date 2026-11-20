# Plan: vLabZone – Docker/containerlab test environment for hardware-pool access

## Context
`idea.md` (German) describes a fully virtual test bed on one Linux host that reproduces the planned
access architecture (vSphere workspaces → corporate firewall → WireGuard lab edge → VLAN switch →
setups with overlapping `192.168.1.0/24` networks), plus a broker/agent prototype and 26 experiments
(E01–E26) in 5 stages. `/home/hauptenb/git/vLabZone` currently only contains `idea.md` and is not a git repo.
Goal: turn this folder into the git project and implement all stages incrementally, each stage ending
with a green, regression-tested pytest suite.

Decisions: full plan, all stages · English for code/README/commits (idea.md stays German) · FastAPI broker ·
**installable on many Linux hosts (bare metal + VMs on any hypervisor), provisioned via Ansible** ·
images built locally on each host · **the real setup uses a MikroTik as lab edge → a virtual
RouterOS CHR edge is added as a selectable alternative to the Linux edge** (`EDGE_IMPL=linux|routeros`).

Current dev host (checked): kernel 7.0, Docker 28.5.1, Python 3.12, 8 vCPU / 15 GB RAM,
containerlab not installed, modules `wireguard`, `vrf`, `8021q` not loaded. It is just one target among many.

## Portability principles
- No hardcoded paths, users or hostnames; repo path, EDGE_MODE, resource profile etc. come from Ansible
  vars / a generated `.env`, with sane defaults.
- All tool versions pinned in one place (`deploy/ansible/group_vars/all.yml`: containerlab, Docker CE,
  base image tag/digest, Python deps via `requirements.txt` with pins).
- Supported targets: Debian 12/13, Ubuntu 24.04 (x86_64; arm64 best effort). Preflight fails early with
  a clear message otherwise (kernel ≥ 5.6, required modules loadable, not Docker Desktop, CPU/RAM/disk
  per profile `base` vs `scale`).
- Idempotent: re-running the playbook converges, never destroys a running lab unless asked (`vlab_redeploy=true`).
- Same Makefile targets work on every host after provisioning; Ansible only installs prerequisites,
  places the repo, builds images and optionally deploys + smoke-tests.
- Existing Docker installs are respected (only installed if missing; `vlab_manage_docker` toggle).
- The MikroTik variant is opt-in per host (`vlab_routeros_enabled`), because it needs KVM: hosts without
  `/dev/kvm` (or VMs without nested virtualization) still run the full Linux-edge lab.

## Repository layout (git root = vLabZone; the lab lives in `containerlab/vlabzone/`)
```
vLabZone/containerlab/vlabzone/
├── idea.md                      # concept (unchanged)
├── README.md                    # quickstart, prerequisites, make targets, experiment index
├── Makefile                     # host-check, build, up, down, reset, test, test-stageN, exp-EXX, pcap
├── .gitignore                   # .venv/, clab-*/, results/**, *.key, __pycache__
├── deploy/ansible/
│   ├── ansible.cfg, requirements.yml        # pinned collections (community.docker, ansible.posix)
│   ├── inventory/{hosts.example.yml,localhost.yml}
│   ├── group_vars/all.yml                   # pinned versions, defaults (repo dir, profile, edge mode)
│   ├── site.yml                             # preflight → host_prereqs → vlabzone [→ lab_deploy]
│   └── roles/
│       ├── preflight/      # distro/kernel/arch/resources/module checks, virt detection (warn on nested quirks)
│       ├── host_prereqs/   # kernel modules + modules-load.d, br_netfilter sysctls, Docker CE (if missing),
│       │                   #   user in docker group, containerlab (pinned .deb/apt repo), python3-venv
│       ├── vlabzone/       # git clone/sync repo to vlab_dir, .env from template, venv + pip, make build
│       ├── routeros_image/ # opt-in: KVM check, download pinned CHR image (version + sha256), build vrnetlab image
│       └── lab_deploy/     # optional: make up + make smoke; tag-gated (--tags deploy)
├── scripts/preflight.sh         # same checks as the role, runnable standalone (`make host-check`)
├── topology/
│   ├── hwpool.clab.yml          # base topology (stage 1–3); lab-edge kind/image chosen via EDGE_IMPL
│   │                            #   (clab env-var templating or a small render step)
│   └── gen_scale.py             # generates hwpool-scale.clab.yml for E21
├── images/
│   ├── base/Dockerfile          # debian:trixie-slim + iproute2 wireguard-tools nftables iptables tcpdump
│   │                            #   openssh socat ser2net iperf3 curl jq python3 python3-yaml
│   ├── router/  (Dockerfile, entrypoint.sh, roles/{corp-fw,switch,gw}.sh, sw-port)
│   ├── edge/    (entrypoint.sh, edge-vrf.sh, edge-pbr.sh, edge-alias.sh, edge-ctl)   # edge-ctl = peer add/del/list used by broker
│   ├── workspace/ (lab-agent/, lab CLI, lab-enter, static-wg/ for stage 1)
│   ├── device/  (entrypoint.sh, ident-http.py, rsyslog.conf, sshd_config)
│   ├── serial/  (ser2net.yaml, fake-console.sh)
│   ├── mgmt/    (tiny bridge node for 10.198.0.0/24)
│   ├── routeros/ (build.sh wrapping vrnetlab for the CHR image, templates/edge-{vrf,pbr,alias}.rsc.j2,
│   │             render.py: setups.yaml → startup-config .rsc)
│   └── broker/  (app/main.py, app/db.py, schema.sql, app/reconcile.py,
│                 drivers/{base,edge_ssh,edge_routeros,switch_ssh}.py, pyproject.toml)
├── config/
│   ├── setups.yaml              # setups, VLANs, nets, switch ports, devices (single source of truth)
│   ├── corp-fw.nft              # the one-time corporate allowance
│   └── users.yaml               # test users + tokens
├── tests/
│   ├── conftest.py, lib/{node.py,wg.py,results.py}
│   └── test_eXX_*.py            # one module per experiment, marked @pytest.mark.stageN / edge_mode
└── results/.gitkeep             # protocols (markdown/json) + pcaps, git-ignored content
```

## Key technical design choices
- **Topology tool:** containerlab, all nodes `kind: linux`, prebuilt images, `network-mode: none`
  everywhere (broker included – it only talks via its veth links). If clab rejects it for a node,
  entrypoint falls back to an nft rule dropping all `eth0` traffic except from the docker host (idea §4.4).
- **Shared L2 segments without Docker IPAM:** `corp-fw` bridges `eth1–eth3` for the workspace net
  (10.20.10.0/24); a small `lab-mgmt` bridge node provides 10.198.0.0/24 for broker/edge/switch.
- **config/setups.yaml** is bind-mounted read-only into edge, switch, broker; entrypoints and broker
  derive VLANs, ports, gateways from it (no duplicated addressing).
- **Edge VRF mode:** per setup `vrf-sN` (table 1000+N), `eth2.<vlan>` with 192.168.1.254/24, `wg-sN`
  on UDP 51800+N with 10.250.0.1/16, both enslaved to the VRF; nft `masquerade` on `oifname eth2.<vlan>`.
  Same IPs in different VRFs are fine; the WG UDP socket stays in the default VRF.
- **Edge PBR mode:** single `wg0` on 51820, `eth2.199` = 10.199.0.1/24, per context
  `ip rule from 10.250.u.s iif wg0 lookup <table N>`; table N default via 10.199.0.N (`gw-N`), `gw-N`
  masquerades into its setup VLAN and routes 10.250/16 back via 10.199.0.1.
- **Alias:** nft `dnat ip prefix to` / NETMAP 10.200.N.0/24 ↔ 192.168.1.0/24 on the edge for `ws-ci` contexts.
- **Workspaces:** desktop mode = one `wg-sN` interface + route 192.168.1.0/24; power mode = WG interface
  created in init netns then moved into netns `setupN` (UDP socket stays outside → isolated from corp);
  alias mode = routes 10.200.N.0/24 per tunnel.
- **Devices:** wrong default gateway 192.168.1.1 on purpose; `ident-http.py` returns
  `{setup, device, ip, peer}`; rsyslog logs SSH logins with source IP.
- **Peers only via broker/scripts:** edge starts with no peers (stage 1: `edge-ctl peer add` called by
  test fixtures; stage 3: broker calls the same `edge-ctl` over SSH → one code path).
- **Tests:** pytest from a host venv, `docker exec clab-hwpool-<node>` helpers; `EDGE_MODE` parametrises
  runs (`make test EDGE_MODE=pbr`). Each test writes a protocol to `results/` using the §8 fields.
- containerlab needs root → Makefile uses `sudo containerlab …`; tests use docker group access.

## MikroTik (RouterOS CHR) lab edge
- **Node:** containerlab kind `mikrotik_ros`, image `vrnetlab/mikrotik_routeros:<pinned 7.x>` built locally
  from the official CHR disk image (download URL + sha256 pinned in `group_vars`; nothing proprietary in git).
  Runs as QEMU/KVM inside the container: ~256–512 MB RAM, 1 vCPU, boot time 30–90 s → tests wait for readiness.
- **Same wiring as the Linux edge:** containerlab maps `eth1, eth2, …` to `ether2, ether3, …`
  (`ether1` = management). corp side = ether2, trunk to `lab-sw` = ether3, lab-mgmt = ether4. The rest of the
  topology, the addressing and all workspace/device logic stay unchanged.
- **Config from the same source:** `images/routeros/render.py` turns `config/setups.yaml` into a startup
  `.rsc` (clab `startup-config`):
  - VRF mode: per setup `/interface vlan` on ether3, `/interface wireguard wg-sN listen-port=51800+N`,
    `/ip vrf` holding both, `/ip address` 192.168.1.254/24 + 10.250.0.1/16 per VRF,
    `/ip firewall nat masquerade out-interface=vlanN`.
  - PBR mode: one `wg0` (51820), VLAN 199, `/routing table` + `/routing rule src-address=… action=lookup-only-in-table`.
  - Alias: `/ip firewall nat action=netmap` 10.200.N.0/24 → 192.168.1.0/24.
  - Hardening: services limited to REST (www-ssl) + SSH on the lab-mgmt interface; firewall on ether1
    accepts only the containerlab host (bypass test E05 covers it, because `network-mode: none` isn't possible here).
- **Broker driver:** `drivers/edge_routeros.py` uses the RouterOS v7 REST API over lab-mgmt (peer add/remove
  with `allowed-address`, listing peers/rules for reconcile). Interface identical to `edge_ssh.py`, so
  broker, agent and tests don't change. The same driver should later run against the real MikroTik.
- **License limit:** free CHR caps each interface at 1 Mbit/s. All functional tests pass with that.
  Throughput/latency experiments (E15, E17, E21, E22) need a CHR trial/paid license (key passed as an
  Ansible-vault variable, never committed); without one they are skipped with a clear reason.
- **Parity experiment E27:** run the stage 1–3 suites with `EDGE_IMPL=routeros` and compare against the Linux
  edge (behaviour, fail-closed on misconfiguration, config steps per booking, reconcile after reboot).
  Optional: diff the generated `.rsc` against an `/export` of the real router to keep them aligned.

## Stages / implementation steps

### Stage 0 – Repo & host provisioning  ✅ implemented 2026-11-20 (smoke test pending first provisioning run)
1. `git init`, `.gitignore`, `README.md`, `Makefile` skeleton, initial commit (idea.md included).
2. Ansible: `preflight`, `host_prereqs`, `vlabzone`, `lab_deploy` roles + `site.yml`; inventories for
   remote hosts and `localhost` (`ansible-playbook -i inventory/localhost.yml site.yml -K`).
   `scripts/preflight.sh` mirrors the checks for non-Ansible diagnosis.
3. `images/base` + all role images; `make build` tags `hwpool/<role>:<repo version>` + `latest`; build
   args allow an apt mirror / proxy for restricted networks.
4. Validate provisioning on this dev host (local connection) and on one fresh Debian VM; README documents
   both flows and per-host overrides (`host_vars/<host>.yml`).

### Stage 1 – Network skeleton (E01–E06, E09)
5. `corp-fw` role: bridge, routing, `corp-fw.nft` (only UDP 51800–52099 → edge, TCP 443/8443 → broker), knobs for netem/UDP timeout.
6. `lab-sw` role: vlan_filtering bridge, trunk eth1 (112,107,199), `sw-port <if> <vlan>` script.
7. Devices for setup 12 (dev-12-20, dev-12-21) and 7 (dev-7-20, dev-7-21); `corp-svc`.
8. Edge VRF mode + `edge-ctl`; workspaces with static WG configs generated by a fixture.
9. Tests E01–E06, E09 incl. pcap on corp-fw/lab-sw and bypass checks over eth0/mgmt.

### Stage 1b – RouterOS trial (separate step after stage 1)
10. Build the CHR image, boot it in a separate `topology/spike-routeros.clab.yml` (CHR + one workspace,
    switch and device), configure one VRF + WireGuard + VLAN + masquerade path for setup 12 by hand.
    Checks the riskiest assumptions (WireGuard in VRF on RouterOS, clab interface mapping, boot time)
    before stage 2 builds the RouterOS edge on them. Findings in `docs/routeros-spike.md`.

### Stage 2 – Variants (E10, E11, E13, E14, E27 part 1)
11. Edge PBR mode + `gw-12`, `gw-7`; run the stage-1 suite in both modes; deliberate misconfig tests (E10).
12. `lab enter` namespaces in `ws-power` (E11), alias mode in `ws-ci` (E13).
13. `acc-12-ser`: socat pty + fake console + ser2net RFC 2217 on :3001; pyserial client test (E14).
14. RouterOS edge: `render.py` + `.rsc` templates for VRF/PBR/alias, `routeros_image` Ansible role,
    `EDGE_IMPL` switch in Makefile/topology; stage 1–2 suites pass with `EDGE_IMPL=routeros` (peers added
    via REST from the test fixtures).

### Stage 3 – Broker & agent (E07, E08, E12, E18–E20, E27 part 2)
15. FastAPI broker + SQLite (`schema.sql`: users, setups, devices, bookings, contexts, audit), token auth,
    self-signed TLS on 443; endpoints per idea §5.7; drivers behind `drivers/base.py` interface.
16. Reconcile loop (DB desired state vs `wg show dump` / `ip rule`, or RouterOS REST), expiry handling, revoke.
17. Conflict checks: overlapping nets in desktop mode, collision with corporate nets → suggest namespace/alias.
18. `lab-agent` + `lab` CLI (connect/disconnect/list/enter) generating keys locally; replace static WG fixtures.
19. `drivers/edge_routeros.py` (REST API); E18 for RouterOS = CHR reboot followed by reconcile.
20. Tests E07, E08 (revoke ≤ 1 s timing), E12, E18 (edge restart), E19 (workspace recreate), E20 (port move),
    for both edge implementations; E27 parity report.

### Stage 4 – Operations & load (E15–E17, E21, E22; optional E23–E26)
21. netem/MTU/UDP-timeout helpers on corp-fw; E15 MTU sweep → MSS-clamp recommendation; E16 keepalive; E17 latency matrix.
22. `topology/gen_scale.py` for 50 setups / 200 contexts (edge-side devices only) → E21 in VRF vs PBR; E22 iperf3.
    RouterOS runs only with a license (see license limit above); CPU-bound CHR numbers are not
    representative of real MikroTik hardware, so they are documented as such.
23. Optional: `samba-ad` + Kerberos (E23), SOCKS setup-shell (E24), per-VRF DNS (E25), usbip if kernel allows (E26).

Each stage: full regression suite of earlier stages, then commit (one or more commits per step, English messages).

## Verification
- `ansible-lint` + `ansible-playbook site.yml --check` clean; playbook run twice → second run `changed=0`.
- Fresh Debian 12 VM and Ubuntu 24.04 VM: `ansible-playbook -i <inv> site.yml --tags all,deploy` ends with a passing smoke test.
- `make host-check` – modules loaded, containerlab/docker present.
- `make build && make up` – `sudo containerlab inspect -t topology/hwpool.clab.yml` shows all nodes running.
- Manual smoke: `docker exec clab-hwpool-ws-std curl -s http://192.168.1.20` → `"setup": 12`.
- `make test` (stage markers) in `EDGE_MODE=vrf` and `pbr`; `make reset` returns to baseline in seconds.
- `make test EDGE_IMPL=routeros EDGE_MODE=vrf|pbr` passes on a KVM host; on a host without KVM the
  RouterOS tests are skipped with a reason, and the preflight reports that the variant is unavailable.
- Results protocols appear in `results/` per experiment.

## Notes / risks
- `network-mode: none` support in the installed clab version is verified in step 8; fallback documented.
- RouterOS CHR: needs `/dev/kvm` (this dev host has it; VMs need nested virtualization enabled on the
  hypervisor). The free license caps throughput at 1 Mbit/s. RouterOS-specific behaviour (WireGuard
  inside a VRF, the `/routing rule` semantics) is verified in the stage 1b trial. Pin the RouterOS version
  to the one on the real router.
- Docker's own iptables/br_netfilter rules may interfere with bridges inside privileged containers – check
  `br_netfilter` sysctls in host-setup.
- Provisioning and `containerlab deploy` need sudo; on this host I will ask you to run the playbook with `-K` yourself (`! ansible-playbook … -K`).
- VMs on some hypervisors (e.g. vSphere with promiscuous mode off) don't matter here because all lab traffic stays inside the host; only kernel modules must be present (cloud kernels like `linux-image-cloud-amd64` may lack `vrf` → preflight detects and suggests the generic kernel).
