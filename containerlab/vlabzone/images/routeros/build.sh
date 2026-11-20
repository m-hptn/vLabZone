#!/usr/bin/env bash
# Build the RouterOS CHR container image for containerlab (kind mikrotik_ros) via vrnetlab.
#
# Downloads the official CHR VMDK (checksum-verified), clones vrnetlab at a pinned
# commit and runs its build. Nothing proprietary ends up in git; everything lives
# in the cache dir. Idempotent: does nothing if the image already exists.
#
# Usage: build.sh [--version 7.24.6] [--sha256 <zip sha256>] [--cache-dir DIR]
#                 [--vrnetlab-ref <commit>] [--force]
# The Ansible role routeros_image calls this script with the pinned values from group_vars.
set -euo pipefail

VERSION=7.24.6
# sha256 of chr-<VERSION>.vmdk.zip; must match VERSION (default only valid for 7.24.6)
SHA256=30ee295fa1807c3de9819c7308e6b1629cf4bda29e7a42ea27788af75f63fdaf
VRNETLAB_REPO=https://github.com/srl-labs/vrnetlab.git
VRNETLAB_REF=85445edfdf366b3d5f04942a10bb5a7d22df61a7
CACHE_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/vlabzone"
FORCE=false
SHA_GIVEN=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --version) VERSION="$2"; shift 2 ;;
    --sha256) SHA256="$2"; SHA_GIVEN=true; shift 2 ;;
    --cache-dir) CACHE_DIR="$2"; shift 2 ;;
    --vrnetlab-ref) VRNETLAB_REF="$2"; shift 2 ;;
    --force) FORCE=true; shift ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

if [[ "$VERSION" != 7.24.6 && "$SHA_GIVEN" == false ]]; then
  echo "RouterOS $VERSION requested: pass --sha256 of chr-$VERSION.vmdk.zip (or set it in group_vars)" >&2
  exit 2
fi

IMAGE="vrnetlab/mikrotik_routeros:$VERSION"
if [[ "$FORCE" == false ]] && docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "$IMAGE already present – nothing to do (use --force to rebuild)"
  exit 0
fi

mkdir -p "$CACHE_DIR"
ZIP="$CACHE_DIR/chr-$VERSION.vmdk.zip"
URL="https://download.mikrotik.com/routeros/$VERSION/chr-$VERSION.vmdk.zip"

if [[ ! -f "$ZIP" ]] || ! echo "$SHA256  $ZIP" | sha256sum -c --status; then
  echo ">>> downloading $URL"
  curl -fL --retry 3 -o "$ZIP.part" "$URL"
  mv "$ZIP.part" "$ZIP"
fi
echo "$SHA256  $ZIP" | sha256sum -c

SRC="$CACHE_DIR/vrnetlab"
if [[ ! -d "$SRC/.git" ]]; then
  git clone --quiet "$VRNETLAB_REPO" "$SRC"
fi
git -C "$SRC" fetch --quiet origin
git -C "$SRC" checkout --quiet --force "$VRNETLAB_REF"
git -C "$SRC" clean -fdxq mikrotik/routeros

echo ">>> unpacking CHR image"
unzip -o -q "$ZIP" -d "$SRC/mikrotik/routeros"

echo ">>> building $IMAGE with vrnetlab @ ${VRNETLAB_REF:0:12}"
make -C "$SRC/mikrotik/routeros" docker-image

docker image inspect "$IMAGE" >/dev/null
echo ">>> done: $IMAGE"
