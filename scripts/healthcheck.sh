#!/usr/bin/env bash
# shellcheck source=scripts/lib/deployment.sh
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/deployment.sh"
public=auto
case "${1:-}" in
    '') ;;
    --public) public=yes ;;
    --local-only) public=no ;;
    --public-if-configured) ;;
    *) die "Usage: bash scripts/healthcheck.sh [--public|--local-only|--public-if-configured]" ;;
esac
[[ $# -le 1 ]] || die "Too many arguments."
load_runtime
lock_deployment
need curl
failed=0
for service in postgres redis web worker beat; do
    id="$(container_id "$service")"
    if [[ -z "$id" || "$id" == *$'\n'* ]]; then log "UNHEALTHY: expected exactly one $service container."; failed=1; continue; fi
    status="$(docker inspect --format '{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{else}}no-healthcheck{{end}} restarts={{.RestartCount}}' "$id")"
    log "$service: $status"
    [[ "$status" == 'running healthy restarts='* ]] || failed=1
done
dc exec -T web python manage.py deploycheck --check readiness --check worker --check beat --check storage || failed=1
http_health "http://127.0.0.1:$PORT/api/health/" -H "Host: $DOMAIN" -H 'X-Forwarded-Proto: https' || failed=1
static_status="$(curl --silent --show-error --output /dev/null --write-out '%{http_code} %{content_type}' --max-time 15 -H "Host: $DOMAIN" -H 'X-Forwarded-Proto: https' "http://127.0.0.1:$PORT/static/admin/css/base.css" || true)"
[[ "$static_status" == '200 text/css'* ]] || failed=1
configured_public="$(config_get DEPLOY_PUBLIC_HTTPS)"
if [[ "$public" == yes || ( "$public" == auto && ( -f "$STATE_DIR/https-configured" || "$configured_public" == true ) ) ]]; then
    http_health "https://$DOMAIN/api/health/" || failed=1
    private_status="$(curl --silent --output /dev/null --write-out '%{http_code}' --max-time 15 "https://$DOMAIN/api/health/ready/" || true)"
    [[ "$private_status" == 403 || "$private_status" == 404 ]] || { log "UNHEALTHY: public readiness endpoint is not denied."; failed=1; }
    certificate_name="$DOMAIN"
    [[ ! -f "$STATE_DIR/certificate-name" ]] || certificate_name="$(cat "$STATE_DIR/certificate-name")"
    [[ "$certificate_name" =~ ^[a-zA-Z0-9._-]+$ ]] || die "Invalid certificate name in state."
    certificate="/etc/letsencrypt/live/$certificate_name/fullchain.pem"
    if [[ -r "$certificate" ]]; then
        openssl x509 -in "$certificate" -noout -checkend 604800 >/dev/null || failed=1
        openssl x509 -in "$certificate" -noout -checkhost "$DOMAIN" >/dev/null || failed=1
    else log "Cannot verify origin certificate; read permission is required."; failed=1; fi
else log "Public HTTPS verification pending. Configure Host Nginx/SSL, then run healthcheck.sh --public."; fi
mapfile -t ids < <(dc ps -q)
if [[ ${#ids[@]} -gt 0 ]]; then docker stats --no-stream --format 'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}' "${ids[@]}" || failed=1; fi
df -h "$ROOT"
free -m
docker system df
log "Voice worker: not probed (separate LiveKit deployment). Telephony: no calls placed; calling readiness remains separate."
(( failed == 0 )) || die "One or more health checks failed."
log "Backend health checks passed."
