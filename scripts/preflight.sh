#!/usr/bin/env bash
# shellcheck source=scripts/lib/deployment.sh
# Read-only shared-host checks: no host packages/services/certificates change.
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/deployment.sh"
[[ $# -eq 0 ]] || die "Usage: bash scripts/preflight.sh"
for command in docker nginx certbot curl ss free df awk stat getent flock git; do need "$command"; done
docker info >/dev/null 2>&1 || die "Docker is unavailable. Start it or grant Docker access explicitly."
docker compose version >/dev/null || die "Docker Compose v2 is required."
load_runtime
config validate --env-file "$(env_relative)"
dc config --quiet
validate_owned_resources
permissions="$(stat -c %a "$ENV_FILE")"
[[ "$permissions" == 600 || "$permissions" == 400 ]] || die "Environment permissions must be 600 or 400: chmod 600 '$ENV_FILE'."
[[ "$(stat -c %u "$ENV_FILE")" == "$(id -u)" || "$(id -u)" == 0 ]] || die "Environment must belong to the deployment user."
minimum_mb="$(config_get DEPLOY_MIN_AVAILABLE_MB)"
minimum_gb="$(config_get DEPLOY_MIN_FREE_GB)"
build_gb="$(config_get DEPLOY_MIN_BUILD_GB)"
max_swap="$(config_get DEPLOY_MAX_SWAP_PERCENT)"
for value in "$minimum_mb" "$minimum_gb" "$build_gb" "$max_swap"; do [[ "$value" =~ ^[0-9]+$ ]] || die "Resource thresholds must be non-negative integers."; done
available_mb="$(awk '/MemAvailable:/ {print int($2/1024)}' /proc/meminfo)"
cpu="$(getconf _NPROCESSORS_ONLN)"
disk_kb="$(df -Pk "$ROOT" | awk 'NR==2 {print $4}')"
docker_root="$(docker info --format '{{.DockerRootDir}}')"
[[ -d "$docker_root" ]] || die "Cannot inspect Docker storage at $docker_root. Local permissions are required; remote Docker hosts are unsupported."
build_kb="$(df -Pk "$docker_root" | awk 'NR==2 {print $4}')"
swap_percent="$(awk '/SwapTotal:/ {total=$2} /SwapFree:/ {free=$2} END {if(total>0) print int((total-free)*100/total); else print 0}' /proc/meminfo)"
log "Available RAM: ${available_mb} MiB; CPUs: $cpu; free checkout disk: $((disk_kb / 1048576)) GiB; free Docker disk: $((build_kb / 1048576)) GiB; swap used: $swap_percent%."
(( available_mb >= minimum_mb )) || die "Insufficient available RAM (need ${minimum_mb} MiB). Reduce other load or increase capacity."
(( cpu >= 1 )) || die "No CPU capacity detected."
(( disk_kb >= minimum_gb * 1048576 )) || die "Insufficient disk space (need ${minimum_gb} GiB available). Preview cleanup.sh --dry-run."
(( build_kb >= build_gb * 1048576 )) || die "Insufficient Docker build space (need ${build_gb} GiB free)."
(( swap_percent <= max_swap )) || die "Swap pressure is too high for a safe build."
published="$(docker ps -q --filter "publish=$PORT")"
if [[ -n "$published" || -n "$(ss -H -ltn "sport = :$PORT")" ]]; then
    owner="$(docker ps -q --filter "publish=$PORT" --filter "label=com.docker.compose.project=$PROJECT" --filter label=com.docker.compose.service=web)"
    [[ -n "$owner" && "$owner" != *$'\n'* ]] || die "Port $PORT is used by something other than this Voice Agent web service."
    binding="$(docker inspect --format '{{range (index .NetworkSettings.Ports "8000/tcp")}}{{.HostIp}}:{{.HostPort}}{{end}}' "$owner")"
    [[ "$binding" == "127.0.0.1:$PORT" ]] || die "Existing web binding is not exactly loopback:$PORT."
fi
getent ahosts "$DOMAIN" >/dev/null || die "DNS does not resolve $DOMAIN. Create the Cloudflare A record before deployment."
certificate_name="$DOMAIN"
[[ ! -f "$STATE_DIR/certificate-name" ]] || certificate_name="$(cat "$STATE_DIR/certificate-name")"
[[ "$certificate_name" =~ ^[a-zA-Z0-9._-]+$ ]] || die "Invalid certificate name in deployment state."
certificate="/etc/letsencrypt/live/$certificate_name/fullchain.pem"
if [[ -r "$certificate" ]]; then
    need openssl
    openssl x509 -in "$certificate" -noout -checkend 604800 >/dev/null || die "Existing certificate expires within seven days. Review Certbot renewal."
    openssl x509 -in "$certificate" -noout -checkhost "$DOMAIN" >/dev/null || die "Existing certificate does not cover $DOMAIN."
    log "Existing certificate covers $DOMAIN and is valid for at least seven days."
else log "No readable certificate yet; backend may deploy locally. Configure Nginx/SSL explicitly before public use."; fi
if compgen -G '/etc/nginx/sites-enabled/*video*' >/dev/null || compgen -G '/etc/letsencrypt/live/*video*' >/dev/null; then
    log "Possible old Video Studio Nginx/certificate entries remain; review manually. Nothing was removed."
fi
log "Preflight passed. Host services were not changed."
