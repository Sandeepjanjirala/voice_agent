#!/usr/bin/env bash
# Export only the worker's required secrets. This does not contact LiveKit.
# shellcheck source=scripts/lib/deployment.sh
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/deployment.sh"
[[ $# -eq 0 ]] || die 'Usage: bash scripts/prepare-worker.sh'
load_runtime
lock_deployment
config worker-env --env-file "$(env_relative)" --output deploy/.env.worker.production
log 'Review deploy/.env.worker.production privately against the existing worker configuration. Existing output is never overwritten.'
log 'LiveKit Cloud supplies project credentials itself. No worker was created, restarted or deployed.'
