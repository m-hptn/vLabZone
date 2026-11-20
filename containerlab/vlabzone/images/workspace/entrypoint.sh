#!/usr/bin/env bash
# hwpool/workspace – simulated vSphere workspace VM.
#
# Env: WS_IP (10.20.10.11/24), WS_MODE (desktop | power | alias)
# Only connected to the corporate network (default route via corp-fw).
# Stage 1: tunnels are set up by the test helper tests/lib/wg.py;
# lab-agent and the `lab` CLI follow in stages 2/3.
set -euo pipefail
. /usr/local/lib/vlab/common.sh

WS_IP=${WS_IP:?} WS_MODE=${WS_MODE:-desktop}
log "hwpool/workspace ${VLAB_VERSION:-dev} ip=$WS_IP mode=$WS_MODE"

wait_if 60 eth1
ip addr add "$WS_IP" dev eth1
ip link set eth1 up
ip route add default via 10.20.10.1

mkdir -p /run/vlab
echo "$WS_MODE" > /run/vlab/ws-mode
mark_ready
idle
