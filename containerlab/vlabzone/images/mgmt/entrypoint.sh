#!/usr/bin/env bash
# hwpool/mgmt entrypoint – L2 bridge for the lab management net 10.198.0.0/24
# Stage 0 skeleton: the node only stays up. The role logic follows in later stages (see PLAN.md).
set -euo pipefail
echo "hwpool/mgmt ${VLAB_VERSION:-dev} starting (hostname $(hostname))"
exec sleep infinity
