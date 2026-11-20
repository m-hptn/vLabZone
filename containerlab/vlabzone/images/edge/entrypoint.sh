#!/usr/bin/env bash
# hwpool/edge entrypoint – lab edge (EDGE_MODE=vrf|pbr)
# Stage 0 skeleton: the node only stays up. The role logic follows in later stages (see PLAN.md).
set -euo pipefail
echo "hwpool/edge ${VLAB_VERSION:-dev} starting (hostname $(hostname))"
exec sleep infinity
