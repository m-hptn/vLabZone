#!/usr/bin/env bash
# hwpool/router entrypoint – corp-fw, lab-sw, gw-N (ROLE selects the behaviour)
# Stage 0 skeleton: the node only stays up. The role logic follows in later stages (see PLAN.md).
set -euo pipefail
echo "hwpool/router ${VLAB_VERSION:-dev} starting (hostname $(hostname))"
exec sleep infinity
