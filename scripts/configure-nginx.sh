#!/usr/bin/env bash
# Opt-in host changes only. Never edits another site's configuration.
set -Eeuo pipefail
# shellcheck source=scripts/lib/deployment.sh
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/deployment.sh"
apply=false issue=false renewal=false certificate=''
while (( $# )); do
    case "$1" in
        --apply) apply=true ;;
        --issue-certificate) issue=true ;;
        --check-renewal) renewal=true ;;
        --certificate-name) shift; certificate="${1:?Certificate name required}" ;;
        --help) printf '%s\n' 'Preview: bash scripts/configure-nginx.sh' 'Apply existing certificate: sudo bash scripts/configure-nginx.sh --apply' 'Issue only the Voice Agent certificate: sudo bash scripts/configure-nginx.sh --apply --issue-certificate' 'Also verify renewal: add --check-renewal (contacts the staging certificate authority).'; exit 0 ;;
        *) die "Unknown Nginx option: $1" ;;
    esac
    shift
done
load_runtime
config validate --env-file "$(env_relative)"
certificate="${certificate:-$DOMAIN}"
[[ "$certificate" =~ ^[a-zA-Z0-9][a-zA-Z0-9._-]*$ ]] || die 'Invalid certificate name.'
mkdir -p "$ROOT/deploy/generated"
candidate="$ROOT/deploy/generated/$PROJECT-$(date -u +%Y%m%dT%H%M%SZ)-$$.conf"
generate() {
    config nginx --env-file "$(env_relative)" --output "${candidate#"$ROOT"/}" --certificate-name "$certificate" "$@"
}
if [[ "$apply" != true ]]; then
    [[ "$issue" != true && "$renewal" != true ]] || die 'Certificate operations require --apply.'
    generate
    log "Preview generated: $candidate. No host service changed. Review then explicitly use --apply."
    exit 0
fi
[[ "$(id -u)" == 0 ]] || die 'Host configuration needs sudo and explicit --apply.'
for command in nginx certbot openssl systemctl curl install; do need "$command"; done
lock_deployment
validate_nginx() {
    local output
    if ! output="$(nginx -t 2>&1)"; then printf '%s\n' "$output" >&2; return 1; fi
    printf '%s\n' "$output" >&2
    if printf '%s\n' "$output" | grep -Fiq "conflicting server name \"$DOMAIN\""; then
        log "Another Nginx server block claims $DOMAIN. Resolve that hostname conflict before applying this site."
        return 1
    fi
}
validate_nginx
target="/etc/nginx/sites-available/$PROJECT.conf"
link="/etc/nginx/sites-enabled/$PROJECT.conf"
marker="# Managed by Vaani: $PROJECT."
[[ ! -L "$target" ]] || die 'Refusing a symbolic-link site target.'
if [[ -e "$target" ]] && ! grep -Fq "$marker" "$target"; then die "Existing $target is not owned by this installation."; fi
if [[ -e "$link" || -L "$link" ]]; then
    [[ -L "$link" && "$(readlink -f "$link")" == "$target" ]] || die "Existing $link belongs to another configuration."
fi
# Report removed Video Studio remnants, without deleting or modifying them.
find /etc/nginx/sites-available /etc/letsencrypt/live -maxdepth 1 -iname '*studio*' -print 2>/dev/null || true
backup="$STATE_DIR/nginx-before-$(date -u +%Y%m%dT%H%M%SZ)-$$"
had_target=false had_link=false changed=false success=false
hook="/etc/letsencrypt/renewal-hooks/deploy/$PROJECT-nginx.sh"
hook_backup="$backup.hook"
had_hook=false hook_changed=false
if [[ -f "$target" ]]; then cp -p "$target" "$backup"; had_target=true; fi
if [[ -L "$link" ]]; then had_link=true; fi
restore_site() {
    local result=$?
    if [[ "$success" != true && "$hook_changed" == true ]]; then
        if [[ "$had_hook" == true ]]; then cp -p "$hook_backup" "$hook"; else rm -f -- "$hook"; fi
    fi
    if [[ "$success" != true && "$changed" == true ]]; then
        log 'Restoring the previous Voice Agent Nginx configuration.'
        if [[ "$had_target" == true ]]; then cp -p "$backup" "$target"; else rm -f -- "$target"; fi
        if [[ "$had_link" != true ]]; then rm -f -- "$link"; fi
        if nginx -t; then systemctl reload nginx; else log 'Existing Nginx validation failed; running configuration has not been replaced.'; fi
    fi
    exit "$result"
}
trap restore_site EXIT
install_site() {
    changed=true
    install -m 644 "$candidate" "$target"
    if [[ ! -L "$link" ]]; then ln -s "$target" "$link"; fi
    validate_nginx || die 'Candidate failed Nginx validation; restoring previous Voice Agent configuration.'
    systemctl reload nginx
}
cert_dir="/etc/letsencrypt/live/$certificate"
valid_certificate() {
    [[ -r "$cert_dir/fullchain.pem" && -r "$cert_dir/privkey.pem" ]] || return 1
    openssl x509 -in "$cert_dir/fullchain.pem" -checkhost "$DOMAIN" -noout >/dev/null 2>&1 || return 1
    openssl x509 -in "$cert_dir/fullchain.pem" -checkend 604800 -noout >/dev/null 2>&1 || return 1
    openssl verify -CAfile /etc/ssl/certs/ca-certificates.crt -untrusted "$cert_dir/chain.pem" "$cert_dir/cert.pem" >/dev/null 2>&1 || return 1
    [[ "$(openssl x509 -in "$cert_dir/fullchain.pem" -pubkey -noout | openssl sha256)" == "$(openssl pkey -in "$cert_dir/privkey.pem" -pubout | openssl sha256)" ]]
}
if ! valid_certificate; then
    [[ "$issue" == true ]] || die 'No valid matching certificate found. Use --apply --issue-certificate after configuring DNS, or choose --certificate-name for an existing matching certificate.'
    [[ "$certificate" == "$DOMAIN" ]] || die 'Issuance may only manage this exact Voice Agent hostname; renew a shared certificate separately.'
    if [[ -e "$cert_dir/fullchain.pem" ]]; then
        names="$(openssl x509 -in "$cert_dir/fullchain.pem" -ext subjectAltName -noout | tail -n +2 | tr -d '[:space:]')"
        [[ "$names" == "DNS:$DOMAIN" ]] || die 'Existing certificate includes other names; refusing to replace it.'
    fi
    email="$(config_get ACME_EMAIL)"
    [[ "$email" =~ ^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$ ]] || die 'Set ACME_EMAIL privately before certificate issuance.'
    install -d -m 755 "/var/lib/$PROJECT/acme/.well-known/acme-challenge"
    generate --http-only
    install_site
    certbot certonly --non-interactive --agree-tos --email "$email" --webroot \
        --webroot-path "/var/lib/$PROJECT/acme" --cert-name "$DOMAIN" -d "$DOMAIN" --keep-until-expiring
    valid_certificate || die 'Issued certificate did not pass hostname, expiry, chain and key checks.'
    candidate="$ROOT/deploy/generated/$PROJECT-https-$(date -u +%Y%m%dT%H%M%SZ)-$$.conf"
fi
generate
install_site
# A scoped renewal hook is needed because webroot certonly does not reload Nginx.
[[ ! -L "$hook" ]] || die 'Refusing a symbolic-link renewal hook.'
if [[ -e "$hook" ]] && ! grep -Fq "$marker" "$hook"; then die 'Existing renewal hook is not managed by Voice Agent.'; fi
if [[ -f "$hook" ]]; then cp -p "$hook" "$hook_backup"; had_hook=true; fi
install -d -m 755 /etc/letsencrypt/renewal-hooks/deploy
hook_changed=true
{
    printf '#!/bin/sh\n%s\n' "$marker"
    # shellcheck disable=SC2016 # Expand RENEWED_LINEAGE when Certbot runs the hook.
    printf 'case "$RENEWED_LINEAGE" in\n  %s) nginx -t && systemctl reload nginx ;;\nesac\n' "$cert_dir"
} > "$hook"
chmod 755 "$hook"
http_health "https://$DOMAIN/api/health/" --resolve "$DOMAIN:443:127.0.0.1" || die 'Origin did not return the expected healthy Django JSON with HTTP 200.'
status="$(curl --silent --output /dev/null --write-out '%{http_code}' --max-time 10 --resolve "$DOMAIN:443:127.0.0.1" "https://$DOMAIN/api/health/ready/")"
[[ "$status" == 404 ]] || die 'The detailed readiness endpoint is publicly reachable.'
if [[ "$renewal" == true ]]; then certbot renew --cert-name "$certificate" --dry-run; fi
if ! systemctl is-enabled certbot.timer >/dev/null 2>&1 && [[ ! -f /etc/cron.d/certbot ]]; then
    log 'Renewal scheduling was not detected. Verify snap.certbot.renew.timer or configure the existing Certbot scheduler before going live.'
fi
printf '%s\n' "$DOMAIN" > "$STATE_DIR/https-configured"
printf '%s\n' "$certificate" > "$STATE_DIR/certificate-name"
success=true
log 'Voice Agent HTTPS configured and origin verified. EduNexa configuration was not modified.'
log 'Keep Cloudflare Full (strict). Now run bash scripts/healthcheck.sh --public.'
