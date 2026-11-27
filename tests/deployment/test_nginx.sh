#!/usr/bin/env bash
# Run real host-configuration control flow against a disposable mock filesystem.
# No Docker, sudo, certificates, network connections or actual services are used.
set -Eeuo pipefail
repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
sandbox="$(mktemp -d "${TMPDIR:-/tmp}/vaani-nginx-tests.XXXXXX")"
trap 'rm -rf -- "$sandbox"' EXIT
passed=0
failed=0

make_fixture() {
    fixture="$sandbox/$1"
    mkdir -p "$fixture/scripts/lib" "$fixture/deploy/.state" \
        "$fixture/etc/nginx/sites-available" "$fixture/etc/nginx/sites-enabled" \
        "$fixture/etc/letsencrypt/live/voice.example.org" \
        "$fixture/etc/letsencrypt/renewal-hooks/deploy"
    # Only filesystem roots are rewritten. The implementation and branches being
    # exercised are copied verbatim; the shared library supplies mock commands.
    sed -e "s|/etc/nginx|$fixture/etc/nginx|g" \
        -e "s|/etc/letsencrypt|$fixture/etc/letsencrypt|g" \
        -e "s|/etc/ssl|$fixture/etc/ssl|g" \
        -e "s|/etc/cron.d|$fixture/etc/cron.d|g" \
        -e "s|/var/lib/|$fixture/var/lib/|g" \
        -e 's/\r$//' "$repo/scripts/configure-nginx.sh" > "$fixture/scripts/configure-nginx.sh"
    cat > "$fixture/scripts/lib/deployment.sh" <<'LIBRARY'
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
PROJECT=voice-agent
DOMAIN=voice.example.org
PORT=8123
STATE_DIR="$ROOT/deploy/.state"
log() { printf '%s\n' "$*" >&2; }
die() { log "$*"; exit 1; }
load_runtime() { :; }
env_relative() { printf 'fixture.env\n'; }
lock_deployment() { :; }
need() { command -v "$1" >/dev/null; }
id() { printf '0\n'; }
config_get() { printf 'owner@example.org\n'; }
config() {
    local action="$1" output=''
    shift
    [[ "$action" == validate ]] && return 0
    while (( $# )); do
        case "$1" in --output) output="$2"; shift ;; esac
        shift
    done
    [[ -n "$output" ]] || return 1
    printf '# Managed by Vaani: voice-agent.\n# candidate replacement\n' > "$output"
}
nginx() {
    local calls=0
    [[ ! -f "$ROOT/nginx-count" ]] || calls="$(cat "$ROOT/nginx-count")"
    calls=$((calls + 1))
    printf '%s\n' "$calls" > "$ROOT/nginx-count"
    printf 'nginx %s\n' "$*" >> "$ROOT/commands"
    if [[ "${MODE:-}" == duplicate_hostname && "$calls" == 2 ]]; then
        printf '[warn] conflicting server name "voice.example.org" on 0.0.0.0:443, ignored\n' >&2
    fi
    [[ "${MODE:-}" != syntax_failure || "$calls" != 2 ]]
}
systemctl() { printf 'systemctl %s\n' "$*" >> "$ROOT/commands"; }
certbot() { printf 'certbot %s\n' "$*" >> "$ROOT/commands"; return 99; }
openssl() {
    case " $* " in
        *' -pubkey '*|*' -pubout '*|*' sha256 '*) printf 'matching-fixture-public-key\n' ;;
    esac
}
curl() {
    printf 'curl\n' >> "$ROOT/commands"
    [[ "${MODE:-}" != origin_failure ]] || return 22
    case "${*: -1}" in
        */api/health/ready/) printf '404' ;;
        */api/health/)
            if [[ "${MODE:-}" == unexpected_health ]]; then printf 'unrelated application\n200';
            elif [[ "${MODE:-}" == health_redirect ]]; then printf 'redirect\n301';
            else printf '{"status":"ok","service":"django","database":"ok"}\n200'; fi ;;
    esac
}
LIBRARY
    # Exercise the real shared HTTP validator against the curl boundary above.
    sed -n '/^http_health() {/,/^}/p' "$repo/scripts/lib/deployment.sh" >> "$fixture/scripts/lib/deployment.sh"
    target="$fixture/etc/nginx/sites-available/voice-agent.conf"
    link="$fixture/etc/nginx/sites-enabled/voice-agent.conf"
    hook="$fixture/etc/letsencrypt/renewal-hooks/deploy/voice-agent-nginx.sh"
    printf '# Managed by Vaani: voice-agent.\n# original site\n' > "$target"
    cp "$target" "$fixture/original-site"
    ln -s "$target" "$link"
    printf '# Managed by Vaani: voice-agent.\n# original hook\n' > "$hook"
    cp "$hook" "$fixture/original-hook"
    printf '# unrelated EduNexa configuration must remain unchanged\n' > "$fixture/etc/nginx/sites-available/edunexa.conf"
    cp "$fixture/etc/nginx/sites-available/edunexa.conf" "$fixture/original-edunexa"
    touch "$fixture/etc/letsencrypt/live/voice.example.org/"{fullchain,privkey,chain,cert}.pem
}

run_configuration() {
    local mode="$1"
    shift
    status=0
    MODE="$mode" bash "$fixture/scripts/configure-nginx.sh" "$@" > "$fixture/output" 2>&1 || status=$?
}

unchanged_unrelated() {
    cmp -s "$fixture/original-edunexa" "$fixture/etc/nginx/sites-available/edunexa.conf"
    ! grep -q '^certbot ' "$fixture/commands" 2>/dev/null
}

test_invalid_candidate_restores_existing_site() {
    make_fixture existing-invalid
    run_configuration syntax_failure --apply
    [[ "$status" != 0 ]]
    cmp -s "$fixture/original-site" "$target"
    cmp -s "$fixture/original-hook" "$hook"
    [[ "$(readlink "$link")" == "$target" ]]
    [[ "$(grep -c '^systemctl reload nginx$' "$fixture/commands")" == 1 ]]
    unchanged_unrelated
}

test_invalid_first_install_removes_only_its_candidate() {
    make_fixture fresh-invalid
    rm -- "$link" "$target"
    run_configuration syntax_failure --apply
    [[ "$status" != 0 && ! -e "$target" && ! -L "$link" ]]
    unchanged_unrelated
}

test_unowned_site_is_untouched() {
    make_fixture unowned
    printf '# not a Vaani site\n' > "$target"
    cp "$target" "$fixture/original-site"
    run_configuration success --apply
    [[ "$status" != 0 ]]
    cmp -s "$fixture/original-site" "$target"
    ! grep -q '^systemctl ' "$fixture/commands"
    unchanged_unrelated
}

test_preview_has_no_host_effects() {
    make_fixture preview
    run_configuration success
    [[ "$status" == 0 && ! -f "$fixture/commands" ]]
    cmp -s "$fixture/original-site" "$target"
    cmp -s "$fixture/original-hook" "$hook"
    [[ "$(find "$fixture/deploy/generated" -type f | wc -l)" == 1 ]]
}

test_existing_certificate_never_triggers_issuance() {
    make_fixture success
    run_configuration success --apply --issue-certificate
    [[ "$status" == 0 ]]
    grep -q 'candidate replacement' "$target"
    [[ "$(cat "$fixture/deploy/.state/https-configured")" == voice.example.org ]]
    unchanged_unrelated
}

test_failed_origin_restores_existing_hook_as_well_as_site() {
    make_fixture origin-failure
    run_configuration origin_failure --apply
    [[ "$status" != 0 ]]
    cmp -s "$fixture/original-site" "$target"
    cmp -s "$fixture/original-hook" "$hook"
    unchanged_unrelated
}

test_failed_origin_removes_only_a_newly_created_hook() {
    make_fixture origin-failure-new-hook
    rm -- "$hook"
    run_configuration origin_failure --apply
    [[ "$status" != 0 && ! -e "$hook" ]]
    cmp -s "$fixture/original-site" "$target"
    unchanged_unrelated
}

test_certificate_actions_require_explicit_apply() {
    local option
    for option in --issue-certificate --check-renewal; do
        make_fixture "without-apply-${option#--}"
        run_configuration success "$option"
        [[ "$status" != 0 && ! -f "$fixture/commands" ]]
        cmp -s "$fixture/original-site" "$target"
        cmp -s "$fixture/original-hook" "$hook"
    done
}

test_symlink_hook_never_changes_its_destination() {
    make_fixture symlink-hook
    mv "$hook" "$fixture/other-site-hook"
    cp "$fixture/other-site-hook" "$fixture/original-other-hook"
    ln -s "$fixture/other-site-hook" "$hook"
    run_configuration success --apply
    [[ "$status" != 0 ]]
    cmp -s "$fixture/original-other-hook" "$fixture/other-site-hook"
    cmp -s "$fixture/original-site" "$target"
    unchanged_unrelated
}

test_duplicate_hostname_warning_rejects_candidate_before_reload() {
    make_fixture duplicate-hostname
    run_configuration duplicate_hostname --apply
    [[ "$status" != 0 ]]
    cmp -s "$fixture/original-site" "$target"
    cmp -s "$fixture/original-hook" "$hook"
    # Only the rollback reload is permitted; the conflicting candidate was never loaded.
    [[ "$(grep -c '^systemctl reload nginx$' "$fixture/commands")" == 1 ]]
    [[ ! -e "$fixture/deploy/.state/https-configured" ]]
    unchanged_unrelated
}

test_nonmatching_health_response_restores_site() {
    local mode
    for mode in unexpected_health health_redirect; do
        make_fixture "$mode"
        run_configuration "$mode" --apply
        [[ "$status" != 0 ]]
        cmp -s "$fixture/original-site" "$target"
        cmp -s "$fixture/original-hook" "$hook"
        [[ ! -e "$fixture/deploy/.state/https-configured" ]]
        unchanged_unrelated
    done
}

for test in \
    test_invalid_candidate_restores_existing_site \
    test_invalid_first_install_removes_only_its_candidate \
    test_unowned_site_is_untouched \
    test_preview_has_no_host_effects \
    test_existing_certificate_never_triggers_issuance \
    test_failed_origin_restores_existing_hook_as_well_as_site \
    test_failed_origin_removes_only_a_newly_created_hook \
    test_certificate_actions_require_explicit_apply \
    test_symlink_hook_never_changes_its_destination \
    test_duplicate_hostname_warning_rejects_candidate_before_reload \
    test_nonmatching_health_response_restores_site; do
    # Calling a function in `if` disables errexit inside it. Run a separate shell
    # instead so a failed intermediate assertion cannot be hidden by a later one.
    export -f "$test" make_fixture run_configuration unchanged_unrelated
    export repo sandbox
    if bash -Eeuo pipefail -c "$test"; then
        printf 'PASS %s\n' "$test"
        passed=$((passed + 1))
    else
        printf 'FAIL %s\n' "$test" >&2
        failed=$((failed + 1))
    fi
done
printf '%s passed; %s failed\n' "$passed" "$failed"
[[ "$failed" == 0 ]]
