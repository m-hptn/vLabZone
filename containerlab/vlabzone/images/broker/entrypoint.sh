#!/usr/bin/env bash
# hwpool/broker – broker prototype.
#
#   eth1  corporate side 10.20.98.2/30 (only TCP 443/8443 arrive here)
#   eth2  lab management 10.198.0.1/24 (controls edge and switch from stage 3)
# Stage 1: plain HTTP health endpoint on 8443. TLS and the API follow in stage 3.
set -euo pipefail
. /usr/local/lib/vlab/common.sh

log "hwpool/broker ${VLAB_VERSION:-dev}"
wait_if 60 eth1 eth2
ip addr add 10.20.98.2/30 dev eth1 && ip link set eth1 up
ip route add 10.20.0.0/16 via 10.20.98.1
ip addr add 10.198.0.1/24 dev eth2 && ip link set eth2 up

mark_ready
cd /opt/broker
exec python3 -m uvicorn app.main:app --host 0.0.0.0 --port "${BROKER_PORT:-8443}"
