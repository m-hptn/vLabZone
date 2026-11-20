# Shared shell helpers for all vLabZone nodes. Source with:  . /usr/local/lib/vlab/common.sh
# shellcheck shell=bash

VLAB_CONFIG_DIR=${VLAB_CONFIG_DIR:-/etc/vlab}
VLAB_READY=/run/vlab-ready

log() { echo "[$(date +%H:%M:%S)] $(hostname): $*"; }
die() { log "ERROR: $*"; exit 1; }

# wait_if <timeout-seconds> <if>... – containerlab creates the links after the
# container started, so every entrypoint waits for its interfaces first.
wait_if() {
  local timeout=$1; shift
  local deadline=$((SECONDS + timeout)) ifc
  for ifc in "$@"; do
    until [[ -e /sys/class/net/$ifc ]]; do
      (( SECONDS < deadline )) || die "interface $ifc did not appear within ${timeout}s"
      sleep 0.2
    done
  done
}

# Optional interfaces: returns 0 if it shows up within the timeout
has_if() {
  local ifc=$1 timeout=${2:-5} deadline=$((SECONDS + ${2:-5}))
  until [[ -e /sys/class/net/$ifc ]]; do
    (( SECONDS < deadline )) || return 1
    sleep 0.2
  done
}

sysctl_set() { echo "$2" > "/proc/sys/${1//.//}" 2>/dev/null || log "WARN: cannot set $1"; }

# Signal readiness to `scripts/wait-ready.sh` and the test suite
mark_ready() { touch "$VLAB_READY"; log "ready"; }

# Keep the container alive; PID 1 is tini
idle() { exec sleep infinity; }
