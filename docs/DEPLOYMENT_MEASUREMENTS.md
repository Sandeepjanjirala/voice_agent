# Local production-image and runtime verification

Measured on 2026-11-27 with Docker Desktop's Linux engine 29.9.3 on the development
machine. These initial measurements used the full local working tree, including
uncommitted dashboard changes. They predate the final PR's dependency, container
and recovery hardening; see [PR_REVIEW.md](PR_REVIEW.md) for exact candidate
validation. The initial backend image, five-service synthetic stack, persistence
and backup/restore checks passed. No VPS, LiveKit Cloud deployment, real call or paid
provider session was used. Local results do not establish capacity on the shared VPS.

## Actual image sizes

Sizes are Docker's uncompressed `image inspect .Size` values, not download sizes.
The original image was built from a captured pre-change source context.

| Image | Bytes | MiB | Installed runtime packages |
| --- | ---: | ---: | ---: |
| Original backend | 1,383,046,491 | 1,319.0 | 125 |
| Final optimized backend | 519,045,978 | 495.0 | 85 |
| Separate voice worker | 1,031,899,102 | 984.1 | 75 |

The backend reduction is **864,000,513 bytes (62.47%)**. The original image already
excluded development dependencies, but installed the voice-processing stack,
including `livekit-local-inference`, `livekit-plugins-ai-coustics`, `av`, `numpy`
and the Agents runtime. The final backend excludes those packages, voice plugins,
PyTorch, pytest, Ruff and uv. It retains `livekit-api`, the existing assistant,
spreadsheet imports, PostgreSQL and S3 recording support. Web, worker, Beat and
migration share the backend image layers; the size is not multiplied by four.

Retained tags are `vaani-deploy-qa-baseline:local`,
`vaani-deploy-qa-optimized:20261127111108` and
`vaani-deploy-qa-voice:20261127111108`. The optimized tag was rebuilt after the
Gunicorn correction below; its final image ID starts `460b1cd53cd1`.

The separate voice image ran an agent-module import, CLI `--help`, and
`python -m livekit.agents download-files` successfully with networking disabled,
a read-only root filesystem and offline model-cache settings. Both application
images run as UID 10001. No voice worker `start` or `dev` session ran.

## Synthetic runtime and recovery

Project `vaani-deploy-qa-20261127111108` used new volumes, generated synthetic
secrets and disabled dialing. The application's development `.env.local` and
existing project volumes were not used. The QA web port was `127.0.0.1:55375`.
The real shared Compose file was used with isolated QA overrides; the web service
had a normal bridge for Docker Desktop host-port access, while database/jobs
also used the QA internal network. Production networking was not changed for QA.

| Check | Observed result |
| --- | --- |
| Shared and dedicated Compose parsing | Both passed `docker compose config --quiet` with fixtures |
| Startup | Migration exited 0; 54 migrations applied; all five services healthy |
| Runtime privileges | UID 10001; application root read-only; private storage mode 0700 |
| Publication | Only web published, bound to 127.0.0.1; database/cache/jobs private |
| HTTP smoke | Readiness, login and WhiteNoise CSS returned 200 with `nosniff`; plain HTTP redirected 301 |
| Private storage | Direct and traversal-style recording HTTP paths returned 404 |
| Redis bound | 96 MiB `maxmemory`, `noeviction` policy |
| Application checks | `deploycheck` readiness, storage, worker and Beat checks passed |
| Django security checks | `check --deploy` exited 0 with the HSTS warning described below |
| Database backup | 245,400-byte custom-format dump validated and restored into a new isolated PostgreSQL database |
| Restored data | 54 migrations and one encrypted synthetic contact; decryption succeeded with original synthetic keys |
| Private-object backup | 265-byte archive of an encrypted synthetic object; archived payload decrypted successfully |
| Redis restart | Ephemeral marker disappeared; worker/Beat delivery recovered |
| Service restarts | Exact encrypted-contact ciphertext and private-object hash persisted |
| Container recreation | PostgreSQL, web, worker and Beat IDs changed; volume names and data hashes remained identical |
| Repeated Compose up | Same container IDs retained after repeated `up --no-deps --no-build` |

The backup contains synthetic fixtures only. Its SHA-256 is
`fe752cead792e6f1e585ecafe9233a47169bfd634cdfb2b33f509037e34e14d7`.
The restore database had no network. These checks exercised direct Compose and
`pg_dump`/`pg_restore` operations; they are **not an end-to-end execution of
`scripts/deploy.sh` or the host backup scripts on Ubuntu**.

`manage.py check --deploy` reported `security.W005` because
`SECURE_HSTS_INCLUDE_SUBDOMAINS` remains false. Applying HSTS to unrelated
subdomains requires an owner decision. HSTS preload also remains off; the
existing `security.W021` suppression is unchanged. The warning was recorded,
not treated as a warning-free result.

## Resource and HTTP measurements

Memory values come from `docker stats --no-stream`. Aggregate peaks sum services
within each snapshot, then take the maximum; they are not the sum of unrelated
per-service maxima. CPU percentages use 100% for one fully occupied logical core.

| Workload | Window | HTTP 200 responses | Transport failures / retries | Median / p95 | Aggregate RAM | Aggregate peak CPU |
| --- | ---: | ---: | --- | --- | ---: | ---: |
| Idle | 3 snapshots | N/A | N/A | N/A | 421.28 MiB mean | N/A |
| Public login/static/readiness | 30 s, 4 clients | 1,590 | 0 / 0 | 63 / 125 ms | 483.07 MiB peak | 42.43% |
| Pre-fix authenticated diagnostic | 30 s, 4 clients | 2,005 | 1 / 1; no terminal error | 47 / 78 ms | 451.53 MiB peak | 107.68% |
| Final corrected authenticated acceptance | 43.41 s, 4 clients | **3,200 / 3,200** | **0 / 0** | **47 / 78 ms** | **471.17 MiB peak** | **130.82%** |

An earlier authenticated attempt aborted on a connection failure before writing
a complete summary; it is preserved separately and is not included in the
diagnostic retry sample or final acceptance totals. Public and diagnostic samples
preceded the final Gunicorn configuration change. The final sample allowed no
retries and collected ten resource snapshots.

Authenticated clients made read-only GETs to `/home/`, `/employees/`,
`/conversations/` and `/make-calls/` using a synthetic administrator session.
The database held one organisation, one contact, no employees and no calls.
No action/provider route was invoked. Four warm-up requests preceded each run.

| Service | Idle mean RAM (MiB) | Idle mean CPU | Final peak RAM (MiB) | Final mean CPU | Final peak CPU |
| --- | ---: | ---: | ---: | ---: | ---: |
| Web | 129.17 | 0.03% | 134.00 | 72.45% | 73.88% |
| Celery worker | 146.77 | 0.21% | 168.20 | 4.35% | 41.25% |
| Beat | 121.43 | 0.00% | 161.90 | 2.12% | 14.91% |
| PostgreSQL | 18.39 | 0.02% | 24.91 | 16.93% | 19.07% |
| Redis | 5.52 | 0.43% | 5.89 | 0.81% | 2.73% |

The final web process was close to its 0.75-CPU cap. The simultaneous aggregate
peak exceeded one core on this multicore development machine. These short runs
with small fixtures do not prove spare CPU on a 1-CPU shared VPS, long-term memory
stability, large-database dashboard performance or concurrent voice-call capacity.
Repeat representative measurements on the VPS after deployment is approved.

Configured memory caps remain web 512 MiB, worker 512 MiB, Beat 192 MiB,
PostgreSQL 384 MiB and Redis 160 MiB; migration has a separate 512 MiB cap.
Caps are limits, not measured usage or guaranteed available host resources.

## Gunicorn failure found and corrected

The first authenticated run encountered `http.client.RemoteDisconnected` while
Gunicorn 23.0.0's single `gthread` worker recycled at the previous request limit
of 1,000 plus jitter up to 100. PID 8 logged its automatic restart and exit at
2026-11-27 16:53:07 +05:30; PID 36 booted at 11:23:07 UTC. Readiness subsequently
returned 200 at 16:53:32 +05:30. The container had zero restarts, was not OOM-killed
and logged no HTTP 5xx response. This was a transport failure, not a logged 500.

Installed-source review found that gthread shutdown waits for submitted request
futures but can close accepted sockets that have not yet become readable. The
connection failure coincided with worker recycling; individual socket causation
was inferred from timing and source review, not proven by a packet trace. A
separate diagnostic run recorded one failed first attempt and one successful
GET retry; that failure is retained rather than hidden by its eventual 200.

The configuration now sets `max_requests = 0` and `max_requests_jitter = 0`,
with a regression assertion for the single-worker defaults. Planned releases
still replace workers. The rebuilt image then completed all 3,200 authenticated
requests without retries, exceeding the former recycling threshold. Before final
shutdown its log contained exactly one worker boot, zero worker exits and zero
automatic recycling events; the container was healthy with zero restarts,
`OOMKilled=false` and zero logged 5xx responses. Disabling request-count recycling
does not establish long-term memory stability; that remains an operational check.

## Additional validation

- Full isolated PostgreSQL 17.11 suite: 1,131 passed, two Windows-specific skips,
  352 warnings, 101.85 seconds. This preceded the final recycling regression test.
- Final dependency/process-boundary and deployment configuration subset: 10 passed,
  including the recycling regression. Ruff checks passed.
- Synthetic configuration CLI: 39 passed, one Windows symlink-privilege skip.
- Nginx host-control-flow tests: nine passed using mocked commands and host paths.
- Actual Nginx 1.31.6 container: generated HTTP and HTTPS configurations passed
  `nginx -t`; seven routing/header checks passed, covering redirects, ACME,
  private paths, forwarded-header scrubbing, singleton security headers, static
  proxying and the 64 MiB request limit. This used a self-signed certificate and
  no network or host ports. Certbot and the target Ubuntu Nginx version were not tested.

## Docker storage recovery and preservation

The initial optimized build stopped after dependency installation because C: had
zero free bytes and Docker's storage became read-only. The owner recovered the
engine and authorized moving Docker Desktop storage to D: before further QA.
No existing images, volumes or application data were pruned to recover space.

Docker was stopped gracefully for a new cold disk backup, verified by SHA-256
against its source. Read-only before/after volume-content inventories and
container/image/volume inventories matched after the supported move. Previously
running unrelated services were restored, and all existing backups were retained.
Machine-specific disk paths, inventory and backup details are kept in the owner's
private local report. QA resumed only after migration verification.

## Evidence and shutdown

Local evidence is retained under the ignored
`.test-tmp/deployment-runtime/case/` directory. It includes image/package reports,
runtime probes, security-check output, synthetic backups, restart/recreation
results, raw resource samples and final lifecycle/shutdown records. The first
failure is in `dashboard-first-run-failure.json` and `dashboard-first-run-web.log`;
the retry diagnostic is in `dashboard-load-stats.json`; the corrected no-retry
run is in `dashboard-fixed-load-stats.json`, `dashboard-fixed-resource-summary.json`
and `dashboard-fixed-lifecycle.json`. Actual Nginx evidence is separately in
`.test-tmp/nginx-actual-20261127/`.

The ignored local harness was invoked from the repository root with:

```powershell
python -m uv run --offline --no-sync python .test-tmp/deployment-runtime/qa.py build --migration-verified
python -m uv run --offline --no-sync python .test-tmp/deployment-runtime/qa.py dashboard-fixed --migration-verified
python -m uv run --offline --no-sync python .test-tmp/deployment-runtime/qa.py probe --migration-verified
python -m uv run --offline --no-sync python .test-tmp/deployment-runtime/qa.py stop --migration-verified
```

Other completed phases were `start`, `measure`, `backup`, `restart`, `recreate`,
`voice-build` and `dashboard-measure`. Each used the same unique synthetic project.
The final probe passed before shutdown. At **2026-11-27 11:30:58 UTC**, the QA
project had **zero running containers**; all seven retained containers, including
migration and restore, had exited with code 0. Only this QA project was stopped.
Its images, containers, named volumes, synthetic backups and logs remain available
for review; existing application containers and data were left intact.
