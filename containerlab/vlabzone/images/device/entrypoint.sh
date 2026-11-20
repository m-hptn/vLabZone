#!/usr/bin/env bash
# hwpool/device entrypoint – simulated lab device (SETUP, IP)
# Stage 0 skeleton: the node only stays up. The role logic follows in later stages (see PLAN.md).
set -euo pipefail
echo "hwpool/device ${VLAB_VERSION:-dev} starting (hostname $(hostname))"
exec sleep infinity
