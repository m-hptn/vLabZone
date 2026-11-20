#!/usr/bin/env bash
# hwpool/workspace entrypoint – workspace VM (WS_MODE=desktop|power|alias) with lab-agent
# Stage 0 skeleton: the node only stays up. The role logic follows in later stages (see PLAN.md).
set -euo pipefail
echo "hwpool/workspace ${VLAB_VERSION:-dev} starting (hostname $(hostname))"
exec sleep infinity
