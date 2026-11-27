#!/bin/sh
# Compatibility entry point for the shared-VPS backup workflow.
set -eu
ROOT=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
exec bash "$ROOT/scripts/backup.sh" "$@"
