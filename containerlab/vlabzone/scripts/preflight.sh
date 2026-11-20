#!/usr/bin/env bash
# Preflight checks for a vLabZone host.
#
#   --stage pre   before provisioning: OS, kernel, modules, resources (docker/containerlab optional)
#   --stage post  after provisioning: additionally docker, containerlab, loaded modules (default)
#   --profile     base | scale   (resource thresholds, see idea.md §3.2)
#   --routeros    also check KVM for the RouterOS CHR edge
#   --mgmt-subnet containerlab management subnet to check for overlaps
#                 (default: $VLAB_MGMT_SUBNET or 172.31.254.0/24)
#
# Exit code: 0 = ok (warnings allowed), 1 = at least one FAIL.
# The Ansible role `preflight` runs this same script, so local and remote checks are identical.
set -uo pipefail

STAGE=post
PROFILE=base
ROUTEROS=false
MGMT_SUBNET="${VLAB_MGMT_SUBNET:-172.31.254.0/24}"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --stage) STAGE="$2"; shift 2 ;;
    --profile) PROFILE="$2"; shift 2 ;;
    --routeros) ROUTEROS=true; shift ;;
    --mgmt-subnet) MGMT_SUBNET="$2"; shift 2 ;;
    -h|--help) sed -n '2,14p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

FAILS=0
WARNS=0
if [[ -t 1 ]]; then G=$'\e[32m'; Y=$'\e[33m'; R=$'\e[31m'; N=$'\e[0m'; else G=; Y=; R=; N=; fi
ok()   { printf '%sPASS%s %s\n' "$G" "$N" "$*"; }
warn() { printf '%sWARN%s %s\n' "$Y" "$N" "$*"; WARNS=$((WARNS+1)); }
fail() { printf '%sFAIL%s %s\n' "$R" "$N" "$*"; FAILS=$((FAILS+1)); }

# --- OS / architecture -------------------------------------------------------
. /etc/os-release 2>/dev/null || true
case "${ID:-}:${VERSION_ID:-}" in
  debian:12|debian:13|ubuntu:24.04) ok "OS ${PRETTY_NAME}" ;;
  debian:*|ubuntu:*) warn "OS ${PRETTY_NAME:-unknown} is not tested (supported: Debian 12/13, Ubuntu 24.04)" ;;
  *) fail "OS ${PRETTY_NAME:-unknown} not supported (Debian 12/13, Ubuntu 24.04)" ;;
esac

ARCH=$(uname -m)
case "$ARCH" in
  x86_64) ok "architecture $ARCH" ;;
  aarch64) warn "architecture $ARCH is best effort" ;;
  *) fail "architecture $ARCH not supported" ;;
esac

VIRT=$(systemd-detect-virt 2>/dev/null); VIRT=${VIRT:-unknown}
[[ "$VIRT" == "none" ]] && ok "running on bare metal" || ok "running virtualized ($VIRT)"

# --- Kernel ------------------------------------------------------------------
KREL=$(uname -r)
KMAJ=${KREL%%.*}; KREST=${KREL#*.}; KMIN=${KREST%%.*}
if (( KMAJ > 5 || (KMAJ == 5 && KMIN >= 6) )); then ok "kernel $KREL (>= 5.6)"; else fail "kernel $KREL is older than 5.6"; fi

for mod in wireguard vrf 8021q bridge; do
  if [[ -d /sys/module/$mod ]]; then
    ok "kernel module $mod loaded"
  elif modinfo "$mod" >/dev/null 2>&1; then
    if [[ "$STAGE" == pre ]]; then ok "kernel module $mod available"; else fail "kernel module $mod available but not loaded (run: sudo modprobe $mod)"; fi
  else
    fail "kernel module $mod missing (cloud kernels often lack vrf – install the generic kernel)"
  fi
done

# --- Resources ---------------------------------------------------------------
if [[ "$PROFILE" == scale ]]; then NEED_CPU=8; NEED_MEM=15; else NEED_CPU=4; NEED_MEM=7; fi
NEED_DISK=20
CPUS=$(nproc)
MEM_GB=$(awk '/MemTotal/ {printf "%d", $2/1024/1024}' /proc/meminfo)
DISK_PATH=/var/lib/docker; [[ -d $DISK_PATH ]] || DISK_PATH=/
DISK_GB=$(df -BG --output=avail "$DISK_PATH" | tail -1 | tr -dc '0-9')
(( CPUS >= NEED_CPU )) && ok "$CPUS vCPU (profile $PROFILE needs $NEED_CPU)" || fail "$CPUS vCPU, profile $PROFILE needs $NEED_CPU"
(( MEM_GB >= NEED_MEM )) && ok "${MEM_GB} GB RAM (profile $PROFILE needs ~$((NEED_MEM+1)))" || fail "${MEM_GB} GB RAM, profile $PROFILE needs ~$((NEED_MEM+1))"
(( DISK_GB >= NEED_DISK )) && ok "${DISK_GB} GB free on $DISK_PATH" || warn "only ${DISK_GB} GB free on $DISK_PATH (recommended $NEED_DISK)"

# --- Docker / containerlab ---------------------------------------------------
check_tool() { # name, required-in-post
  if command -v "$1" >/dev/null 2>&1; then return 0; fi
  if [[ "$STAGE" == pre ]]; then ok "$1 not installed yet (will be installed)"; else fail "$1 not installed"; fi
  return 1
}

if check_tool docker; then
  if INFO=$(docker info --format '{{.OperatingSystem}}|{{.ServerVersion}}' 2>/dev/null); then
    if [[ "$INFO" == *"Docker Desktop"* ]]; then
      fail "Docker Desktop detected – its VM kernel lacks required modules; use Docker Engine on Linux"
    else
      ok "docker engine ${INFO#*|}"
    fi
  elif [[ "$STAGE" == post ]]; then
    warn "docker installed but not usable by $(id -un) (not in group 'docker' or daemon down; re-login after provisioning)"
  fi
fi

if check_tool containerlab; then
  ok "containerlab $(containerlab version 2>/dev/null | awk '/version:/ {print $2; exit}')"
fi

# --- containerlab management subnet ---------------------------------------
# Overlaps with other Docker networks or host routes make `containerlab deploy` fail.
{
  ip -4 route show | awk '$1 ~ /\// {print "route", $1, $3}'
  if command -v docker >/dev/null 2>&1; then
    docker network ls -q 2>/dev/null | xargs -r docker network inspect \
      -f '{{.Name}}{{range .IPAM.Config}} {{.Subnet}}{{end}}' 2>/dev/null | awk '{for (i=2;i<=NF;i++) print "docker", $i, $1}'
  fi
} | python3 -c '
import ipaddress, sys
want = ipaddress.ip_network(sys.argv[1], strict=False)
hits = []
for line in sys.stdin:
    kind, net, name = line.split()[:3]
    if name in ("vlab-mgmt",) or ":" in net:
        continue
    try:
        if ipaddress.ip_network(net, strict=False).overlaps(want):
            hits.append(f"{kind} {name} {net}")
    except ValueError:
        pass
print("; ".join(hits))
sys.exit(1 if hits else 0)
' "$MGMT_SUBNET" > /tmp/.vlab-overlap.$$ 2>&1
if [[ $? -eq 0 ]]; then
  ok "management subnet $MGMT_SUBNET is free"
else
  fail "management subnet $MGMT_SUBNET overlaps: $(cat /tmp/.vlab-overlap.$$) – set VLAB_MGMT_SUBNET / vlab_mgmt_subnet"
fi
rm -f /tmp/.vlab-overlap.$$

# --- KVM (RouterOS CHR) ------------------------------------------------------
if [[ "$ROUTEROS" == true ]]; then
  if [[ -c /dev/kvm ]]; then
    ok "/dev/kvm present (RouterOS CHR possible)"
  else
    fail "/dev/kvm missing – RouterOS edge needs KVM (enable VT-x/AMD-V or nested virtualization on the hypervisor)"
  fi
elif [[ -c /dev/kvm ]]; then
  ok "/dev/kvm present (RouterOS variant could be enabled)"
else
  warn "/dev/kvm missing – only the Linux edge variant can run on this host"
fi

echo
echo "preflight ($STAGE, profile $PROFILE): $FAILS fail, $WARNS warn"
(( FAILS == 0 ))
