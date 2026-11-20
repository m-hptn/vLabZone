#!/usr/bin/env bash
# hwpool/serial entrypoint – serial access instance (ser2net RFC 2217)
# Stage 0 skeleton: the node only stays up. The role logic follows in later stages (see PLAN.md).
set -euo pipefail
echo "hwpool/serial ${VLAB_VERSION:-dev} starting (hostname $(hostname))"
exec sleep infinity
