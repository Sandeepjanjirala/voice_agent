# Production deployment pull request review

This review covers the production-only changes on
`deployment/shared-vps-production` for PR #4. Existing dashboard and workspace
edits remain outside this change. No VPS or LiveKit Cloud deployment, real call,
paid simulation or merge is part of this review.

**Functional validation passed. Security approval remains conditional:** the
final images still have 13 distinct HIGH OS CVEs in the independent Trivy scan.
Scout's zero-HIGH result is incomplete coverage, not a clean security result.
The individual assessments and update actions are in
[OS_SECURITY_REVIEW.md](OS_SECURITY_REVIEW.md); every remaining scanner finding
is retained in [OS_SECURITY_FINDINGS.json](OS_SECURITY_FINDINGS.json).

## Resulting deployment behavior

- A shared-VPS Compose stack runs Django, Celery, PostgreSQL and Redis separately;
  the LiveKit voice worker remains a separate workload.
- Deployment, health, update, backup, restore and rollback commands validate
  project ownership under a lock. Failed resource enumeration stops the command.
- Interrupted database replacement records the archive checksum and preserves a
  safety backup. Resuming requires explicit project confirmation and stopped
  application services.
- Nginx configuration rejects conflicting server names even when its syntax
  check exits successfully, and verifies the application's exact health JSON.
- Images contain only their selected runtime dependencies and run as UID 10001.
  Private configuration, databases, recordings and backups are excluded.
- Redis explicitly runs unprivileged with capabilities dropped. Shared
  application services use a read-only root, capability restrictions and bounded
  resources; the one-off migration service needs writable application files.

## Security hardening

The earlier dependency corrections update aiohttp, cryptography, yarl and PyAV.
CI actions and tooling are pinned, and provider secrets are scoped to the
simulation gate/execution steps rather than dependency installers.

Both application Dockerfiles now use Python 3.12 on Debian Trixie, apply available
Debian updates, and use apt to remove seven unused nonessential packages:
`adduser`, `passwd`, `mount`, `libbsd0`, `libsemanage-common`, `libsemanage2`
and `libsepol2`. Essential packages, native dependencies and package inventory
metadata remain intact. Runtime pip and its unused ensurepip bootstrap archive
are removed as installer functionality; the build stage retains its tools.
The Python version and locked application dependencies are unchanged.

All original 18 HIGH findings were individually checked against Debian records.
The independent scan required nine additional HIGH reviews and assessment of
two baseline CRITICAL matches. The report distinguishes real fixes, absent
components, unchanged vulnerable libraries, severity disagreements and package
mismatches. It also records Trixie-only OS feature exposure and vulnerable
native libraries bundled inside Python wheels. No ignore file, VEX exclusion,
severity override or `ignore-unfixed` filter was used.

| Same-scanner comparison | Bookworm baseline | Final Trixie, each image |
| --- | ---: | ---: |
| Scout OS findings | 100; 18 HIGH, 0 CRITICAL | 27; 0 HIGH/CRITICAL, 1 MEDIUM, 26 LOW |
| Trivy distinct OS CVEs | 170; 29 HIGH, 2 CRITICAL | 99; 13 HIGH, 0 CRITICAL |
| Trivy binary-package matches | 374 | 213 |
| Trivy Python-package findings | 6 in unused global pip | 0 |

The eight originally reviewed source-package issues that Debian still marks
vulnerable are retained in the assessment. Removing the mount CLI does not patch
libmount. Native Kerberos/OpenSSL libraries bundled in psycopg and cryptography
need their own maintenance; a zero Python-package result does not clear them.
Remaining OS findings have no available fixed Trixie package in these scans.
Recheck the official Debian tracker and upstream OpenSSL releases after the
announced 1 December 2026 update window.

## Exact-source validation

Runtime source: `67552f18393a9030cce9d6ae1ae4e6ee62a8a3b9`.
Both final images were rebuilt from a clean Git archive with revision labels and
fresh base resolution. Later report-only commits do not change this source.
The original local environment, database and existing UI/workspace files were
fingerprinted before and after; all 31 checked files were unchanged.

| Check | Verified result |
| --- | --- |
| Complete Linux SQLite suite | 1,127 passed; zero skips |
| Complete Linux PostgreSQL suite | 1,127 passed; zero skips |
| Separate production image probes | Backend 6/6 categories; voice 5/5; both unprivileged, network-disabled and read-only |
| Native library dependencies | All 39 backend and 152 voice objects resolve their shared libraries |
| Native functionality | RTC/Sox resampling, PyAV/Opus encode/decode, ai-coustics FFI, SSL/CA trust, SQLite, compression, XML, timezones and process IPC passed |
| Application functionality | Django WSGI/static assets, libpq, Redis encoding, Celery serialization, Fernet and XLSX round trips passed; Vobiz/LiveKit application paths imported |
| Worker assets | Offline model-file check passed; no provider inference or real calls |
| Fresh Compose startup | Migrations passed; web, worker, Beat, PostgreSQL and Redis all healthy with zero restarts |
| Runtime readiness | Database, cache, migrations, worker, Beat and private storage passed; health JSON and static CSS HTTP 200 |
| Recording recovery | Real synthetic encrypted recording, private 0600 storage, tar/gzip archive, staged recovery and populated-destination refusal passed |
| Database recovery | Real pg_dump/pg_restore into a fresh database verified synthetic data; interrupted restore resumed from original and safety archives |
| Deployment scripts | 13 scripts passed Bash syntax and ShellCheck; 11 Nginx regression cases passed |
| Formatting and workflows | Ruff lint passed; 223 files formatted; both workflows passed actionlint |
| Security scans | Both final images scanned with Docker Scout and checksum-verified Trivy 0.76.2; raw reports and hashes retained |

The complete suite spans both processes, so its Linux test environment uses the
final backend image plus the matching voice dependencies and pytest as read-only
test overlays. Production images remain unchanged and separate. Independent
probes run each unmodified production image to verify that separation.

Both full test runs emitted 317 existing fixture warnings: 315 missing-static
warnings from the fresh source checkout and two short synthetic JWT key warnings.
Production images contain collected static files; their HTTP/static checks
passed. The warnings were recorded rather than suppressed.

Compose tests used new synthetic credentials/data, an internal-only network and
no published ports. QA services were stopped and their synthetic volumes retained.
The interrupted-restore drill executes the committed Bash flow and real
PostgreSQL commands, but scopes Docker lifecycle/configuration/application-idle
checks through fixture adapters. It is not a claim of a full host deployment or
production-database recovery.

CI now builds both production Dockerfiles and runs the native offline probes on
pull requests, alongside the full SQLite/PostgreSQL matrix and shell tests.
GitHub results must match the final PR head before approval.

### Final images

| Image | Tag | Uncompressed bytes | MiB |
| --- | --- | ---: | ---: |
| Backend | `vaani-security-final-backend:67552f18393a-20261127` | 514,017,396 | 490.21 |
| Voice worker | `vaani-security-final-voice:67552f18393a-20261127` | 1,026,299,000 | 978.75 |

Each has 80 installed Debian packages. Image IDs, Dockerfile hashes and raw scan
hashes are in the findings JSON. These local sizes are not registry transfer
sizes or VPS capacity measurements. The earlier load measurements in
[DEPLOYMENT_MEASUREMENTS.md](DEPLOYMENT_MEASUREMENTS.md) predate this candidate
and are separate historical evidence.

## Confidential information

The hardening source and report changes passed Gitleaks. An exact-match review
of all 381 candidate files against 10 locally configured secret values found no
matches. No private environment file, database, recording, backup or
machine-specific inventory is included. Raw evidence is retained outside the
repository.

## Production approval still required

The code and container checks pass, but residual Debian and bundled-library
risks require owner review. Local checks do not establish shared-VPS capacity,
public DNS/TLS, compatibility with existing host sites, Cloud worker containment,
certificate renewal or live provider behavior. Those checks require a separately
approved target-environment deployment and end-to-end call. PR #4 remains a
draft; do not merge or deploy on the strength of a zero-HIGH Scout report.
