#!/usr/bin/env bash
# hwpool/router – one image, several roles (ROLE=corp-fw | switch | gw)
set -euo pipefail
. /usr/local/lib/vlab/common.sh

ROLE=${ROLE:?ROLE must be set (corp-fw, switch, gw)}
log "hwpool/router ${VLAB_VERSION:-dev} role=$ROLE"
[[ -f /usr/local/lib/vlab/roles/$ROLE.sh ]] || die "unknown role $ROLE"
. "/usr/local/lib/vlab/roles/$ROLE.sh"
mark_ready
idle
