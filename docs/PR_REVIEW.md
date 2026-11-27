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

The deployment-only candidate is being tested in an isolated checkout with its
own locked dependencies. Final results will replace this section before the PR
is published. The initial working-tree measurements in
[DEPLOYMENT_MEASUREMENTS.md](DEPLOYMENT_MEASUREMENTS.md) predate these security
corrections and are not a substitute for candidate validation.

## Confidential information review

The staged changes passed Gitleaks and an exact-match check against locally
configured secrets. No private environment file, database, recording or backup
was staged. A full-history scan found three test-fixture alerts, reviewed as
synthetic values rather than credentials. Machine-specific backup inventories
were retained privately and removed from the new public deployment documents.

## Checks reserved for an approved deployment

Local tests do not establish shared-VPS capacity, public DNS/TLS correctness or
live provider behavior. Host port ownership, existing-site compatibility,
certificate issuance/renewal, resource headroom, approved LiveKit deployment and
an approved end-to-end call must be verified in their target environments.
Unfixed base-OS scanner findings require an explicit review of applicable attack
paths; a clean Python dependency audit does not imply a clean OS scan.
