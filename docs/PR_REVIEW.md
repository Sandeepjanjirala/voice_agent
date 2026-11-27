# Production deployment pull request review

This review covers the production-only changes on
`deployment/shared-vps-production`. Earlier dashboard and workspace edits remain
outside this change. No VPS deployment, LiveKit Cloud deployment, real call,
paid simulation or merge is part of this review.

## Scope

- A shared-VPS Compose stack with separate Django, Celery, PostgreSQL and Redis
  processes. The voice worker remains a separate LiveKit workload.
- Guarded configuration, health checks, deployment, update, backup, restore and
  rollback commands, plus an opt-in host Nginx configuration helper.
- Smaller application images, bounded resources and logs, and private data kept
  outside the image build context.
- Production and disaster recovery documentation, deployment regression tests,
  and pinned CI tooling.

## Security corrections during review

- Updated vulnerable aiohttp and cryptography dependencies, their required yarl
  dependency, and the yanked Python 3.11 PyAV patch release. Linux Python 3.11 and
  3.12 dependency audits reported no known advisories after those changes.
- Added the available Debian PAM security update to both application images.
- Made Redis explicitly unprivileged with capabilities dropped; the shell
  wrapper had bypassed its entrypoint's normal privilege drop.
- Applied Docker project ownership checks to recovery and management commands,
  under the deployment lock. Failed resource enumeration now stops the command.
- Added guarded recovery from an interrupted database replacement. Only a
  recorded, unchanged archive can be resumed with explicit project confirmation
  and application services stopped; the pre-restore backup is preserved.
- Reject conflicting Nginx server-name warnings even when `nginx -t` exits
  successfully, and verify the application's exact health response.
- Pinned CI actions to verified release commits and the LiveKit CLI archive to
  its verified checksum. Provider secrets are limited to the simulation gate
  and execution steps, not dependency installers.

## Validation status

Source candidate: `ca860b2f465902869703e9800627a37df34610ba`. Tests and builds use
an isolated checkout with its own environment, installed by `uv 0.13.9` from the
locked dependencies on Python 3.12.10. Subsequent report-only commits do not
change this tested application or deployment source.

| Check | Result |
| --- | --- |
| Full SQLite suite | 1,125 passed; 2 Windows-specific skips |
| Full PostgreSQL 17 suite | 1,125 passed; 2 Windows-specific skips |
| Linux deployment checks | 148 passed, zero skips, inside the candidate backend image as UID 10001 with no network, read-only source/root and no Docker socket |
| Ruff lint and format | Passed; 222 Python files checked for formatting |
| Lock consistency | Passed offline |
| CI workflow syntax | Both workflows passed actionlint; multiline shell blocks passed Bash syntax checks |
| Deployment shell validation | All 13 scripts passed ShellCheck and Bash syntax |
| Nginx regressions | 11 isolated Linux cases passed; actual Nginx duplicate-name warning was rejected |
| Python dependency audits | Linux Python 3.11 and 3.12: 137 packages each, zero known advisories or adverse statuses |
| Image builds | Backend and voice image builds passed from the candidate |
| Voice image offline checks | Agent import, CLI help and cached model-file checks passed with networking disabled |
| Interrupted database recovery | Passed against a fresh PostgreSQL 17.11 fixture: real restore failure, preserved safety archive/state, then successful resume from both the original and safety archives |
| Candidate Compose startup | Fresh migrations passed; web, Celery worker, Beat, PostgreSQL and Redis were all healthy with zero restarts |
| Runtime probes | Database, cache, migrations, worker, Beat and encrypted private storage passed; health JSON and static CSS returned HTTP 200 |

The initial working-tree measurements in
[DEPLOYMENT_MEASUREMENTS.md](DEPLOYMENT_MEASUREMENTS.md) predate these security
corrections and are separate supporting evidence.

Each fresh-checkout full test run reported 315 WhiteNoise warnings because
collected static files are not present in that test checkout and two warnings about short
synthetic JWT fixture keys. Production images collect their static files during
the build; those warnings were recorded rather than suppressed.

The recovery drill executed the frozen Bash recovery flow, pending markers,
checksums and real `pg_dump`, `pg_restore`, `dropdb`, `createdb` and SQL operations.
It injected a real duplicate-table restore failure. Docker lifecycle,
configuration and application-idle checks used scoped fixture adapters, so this
does not claim an end-to-end host deployment or a production database restore.
Test containers were stopped and their synthetic data volumes retained. The
Compose smoke used a fresh project, synthetic credentials, an internal-only
network and no published ports. Its single idle snapshot totalled approximately
443.3 MiB across five services; this is not a VPS capacity or sustained-load test.
The original local environment and database fingerprints remained unchanged.

### Candidate images

Both images run as UID 10001 and contain the PAM `1.5.2-6+deb12u3` update.
Source/lock hashes were checked inside the images. Neither contains a local
environment file; the backend has no runtime database or voice code, and the
voice image has no Django application code.

| Image | Tag | Uncompressed bytes | MiB | Runtime packages |
| --- | --- | ---: | ---: | ---: |
| Backend | `vaani-deploy-pr-backend:ca860b2-20261127` | 524,767,252 | 500.46 | 85 |
| Voice worker | `vaani-deploy-pr-voice:ca860b2-20261127` | 1,037,046,684 | 989.00 | 75 |

The candidate backend is **62.06% smaller** than the measured original backend
(1,383,046,491 bytes). These are local Docker image sizes, not registry transfer
sizes or shared-VPS capacity measurements.

### Remaining operating-system findings

The rebuilt backend and voice Docker Scout scans each report the same **100 raw
advisories across 20 source packages**: 0 critical, 18 high, 23 medium, 50 low and
9 unspecified. Advisory IDs, affected package versions and fix status match
between the images. Both PAM findings were removed; the scanner reports no
available Bookworm fixes for the remaining matches. This is not a clean OS scan
or a production sign-off.

The high matches concern Kerberos (6), OpenSSL (3), util-linux (3), SQLite (3),
zlib (2) and glibc (1). Review found no configured Kerberos/GSS-RPC service,
CMS/DANE API use, attacker-controlled mount configuration, SQLite FTS5/changeset
imports or request-controlled process locale. The shared application containers
drop capabilities and use a read-only root filesystem. These observations narrow
attack paths; they do not prove that every native-library path is unreachable,
and the LiveKit-hosted worker must be assessed against its own runtime controls.

One zlib match, [CVE-2026-76844](https://security-tracker.debian.org/tracker/CVE-2026-76844),
is identified by Debian as a webpack-dev-middleware issue, which is not installed.
The raw total above intentionally retains that scanner match. Retain the scan
and review notes privately, monitor upstream fixes and review residual risk
before approving production use. The PR remains a draft for this review and the
target-environment checks below.

## Confidential information review

The staged changes passed Gitleaks. An exact-match check of all 378 candidate
files against 10 locally configured secret values found no matches. No private
environment file, database, recording or backup was committed. A full-history
scan found three test-fixture alerts, reviewed as
synthetic values rather than credentials. Machine-specific backup inventories
were retained privately and removed from the new public deployment documents.

## Checks reserved for an approved deployment

Local tests do not establish shared-VPS capacity, public DNS/TLS correctness or
live provider behavior. Host port ownership, existing-site compatibility,
certificate issuance/renewal, resource headroom, approved LiveKit deployment and
an approved end-to-end call must be verified in their target environments.
Unfixed base-OS scanner findings require an explicit review of applicable attack
paths; a clean Python dependency audit does not imply a clean OS scan.
