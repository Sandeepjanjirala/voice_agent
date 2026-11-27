#!/usr/bin/env bash
# Run Django management commands in the current backend container.
# shellcheck source=scripts/lib/deployment.sh
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/deployment.sh"
[[ $# -gt 0 ]] || die 'Usage: bash scripts/manage.sh createsuperuser (or another Django management command).'
load_runtime
lock_deployment
[[ ! -e "$STATE_DIR/pending-restore" ]] || die 'Database replacement is incomplete. Complete the explicit restore recovery before management commands.'
[[ -n "$(dc ps --status running -q web)" ]] || die 'Web is not running. Use deploy.sh for initial migrations; recover failed updates before management commands.'
# Compose retains its terminal for interactive createsuperuser; automation disables it.
if [[ -t 0 && -t 1 ]]; then dc exec web python manage.py "$@"; else dc exec -T web python manage.py "$@"; fi
