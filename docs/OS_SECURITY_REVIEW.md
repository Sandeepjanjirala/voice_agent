# OS vulnerability review

Reviewed on **27 November 2026** against each individual Debian CVE record,
ten Debian source-package trackers, the linked Debian Security Advisories,
and the relevant upstream advisories. This review covers all **18 HIGH findings**
in the original Bookworm backend image scan, **nine additional HIGH CVEs** from
an independent Trivy scan, and the baseline's two Trivy CRITICAL matches. It does
not suppress findings or treat a missing application route as a package fix.

The baseline is backend image
`sha256:19a24aeb2fefd6bd7b33b071c824c956b5cf647f2e60881bccf704ecaa5e018a`,
built from application commit `ca860b2`. Its raw Docker Scout OS report contains
**100 findings: 18 HIGH, 23 MEDIUM, 50 LOW and 9 UNSPECIFIED**. The earlier generic
triage described a different, pre-PAM-update scan with 102 findings; those totals
must not be used for this baseline. The retained baseline SARIF SHA-256 is
`7ed046f07812b1c239e0555802119fa9ad0bcd745cd8469cd660f77c8266966b`.

Debian currently records Trixie fixes for **nine** of these findings. **Eight**
remain marked vulnerable on both branches. The remaining finding is a documented
package mismatch: `CVE-2026-76844` concerns webpack middleware, not zlib. These are
review classifications; the raw baseline count remains 18.

**Scout's zero HIGH result on Trixie does not mean all 18 were fixed.** Its
final Trixie report omits the eight original CVEs still marked vulnerable
by Debian. Independent Trivy results also show remaining HIGH findings. Debian
classifies five additional CVEs as Bookworm not affected and Trixie vulnerable:
three util-linux features and two OpenSSL QUIC paths. This is an **OS-package
comparison**, not proof of five newly introduced whole-image exposures: the
baseline backend already bundles OpenSSL 3.5.9, and one util-linux record is
internally inconsistent. The newer base improves support and fixes several
packages, but it is not a security improvement on every path.

## Package and status interpretation

Versions below are **Debian source-package versions**, as used in the scanner and
security tracker. A source-package match can cover several binary packages; it
does not establish that the particular executable or library containing a bug is
installed, loaded, or reachable.

| Source package | Installed Bookworm baseline | Trixie version shown by Debian | Comparison |
| --- | --- | --- | --- |
| [util-linux](https://security-tracker.debian.org/tracker/source-package/util-linux) | `2.38.1-5+deb12u3` | `2.41.5-0+deb13u1` | Fixes two reviewed CVEs; bind-mount source race remains vulnerable. |
| [glibc](https://security-tracker.debian.org/tracker/source-package/glibc) | `2.36-9+deb12u14` | `2.41-12+deb13u5` | Reviewed gettext issue remains vulnerable. |
| [openssl](https://security-tracker.debian.org/tracker/source-package/openssl) | `3.0.22-1~deb12u2` | `3.5.9-1~deb13u1` | All three reviewed CVEs remain vulnerable; security-pocket `3.5.7-1~deb13u3` is also vulnerable. |
| [krb5](https://security-tracker.debian.org/tracker/source-package/krb5) | `1.20.1-2+deb12u5` | `1.21.3-5+deb13u2` in security | Fixes all six reviewed CVEs. Trixie `deb13u1` is still vulnerable. |
| [zlib](https://security-tracker.debian.org/tracker/source-package/zlib) | `1:1.2.13.dfsg-1` | `1:1.3.dfsg+really1.3.1-1+deb13u1` | Fixes the reviewed `gz*` write bug; the other scanner match concerns another product. |
| [sqlite3](https://security-tracker.debian.org/tracker/source-package/sqlite3) | `3.40.1-2+deb12u2` | `3.46.1-7+deb13u2` | All three reviewed CVEs remain vulnerable. |

**Vulnerable** means Debian has not recorded a fix for that release/version.
**No DSA / minor issue** and **postponed** are Debian maintenance decisions, not
statements that a package is unaffected. **No identified route** below is a
bounded source/configuration assessment, not proof that every native dependency
is unreachable. **Package mismatch** means the CVE describes another product;
the original scanner result is retained for audit.

## Original 18 findings

Every CVE link points to its separately retrieved official Debian record.
"Backend" includes Django, Celery worker and Beat. "Voice" means the separate
LiveKit image; shared-VPS Compose restrictions must not be assumed to apply to a
Cloud worker.

| CVE / source package | Bookworm baseline status | Trixie status / fix | Actual trigger and effect | Application/worker exposure and action |
| --- | --- | --- | --- | --- |
| [CVE-2026-53613](https://security-tracker.debian.org/tracker/CVE-2026-53613), util-linux | `2.38.1-5+deb12u3`: vulnerable | Fixed in `2.41.5-0+deb13u1`; [DSA-6442-1](https://www.debian.org/security/dsa-6442-1) | Local user swaps an attacker-writable ancestor of an fstab-authorized mount target, redirecting a privileged mount and potentially gaining root. The upstream description also identifies a legacy fallback without `/proc`. | Neither application invokes mount or accepts fstab configuration. Shared backend drops all capabilities and forbids privilege escalation. These controls reduce this path but do not patch libmount. Use the fixed Trixie package and verify actual runtime permissions; Cloud capability settings require separate verification. |
| [CVE-2026-53615](https://security-tracker.debian.org/tracker/CVE-2026-53615), util-linux | `2.38.1-5+deb12u3`: vulnerable | Fixed in `2.41.5-0+deb13u1`; upstream fix starts at `2.41.4` | A crafted DOS extended-partition image triggers integer wrap/bounds errors in libblkid and false overlapping partition metadata. Further damage requires a consumer to act on that metadata. | Backend and voice do not probe uploaded disk images or expose block devices. Recording uploads and archives are not partition images. Use the fixed package; retain the absence of block-device passthrough. This is not an established direct HTTP code-execution path. |
| [CVE-2026-78410](https://security-tracker.debian.org/tracker/CVE-2026-78410), util-linux | `2.38.1-5+deb12u3`: vulnerable | `2.41.5-0+deb13u1`: vulnerable, no DSA/minor issue; unstable fixed at `2.42.3-1` | Local user replaces an fstab-authorized bind source before privileged mount. `X-mount.owner`, group or mode options can then change the wrong inode as root. | No application mount workflow or attacker-controlled fstab exists. Backend capability/privilege restrictions reduce exposure; they do not remove the vulnerable package. Prefer removing unnecessary mount utilities where dependency-safe and keep watching the stable fix. No Cloud containment claim without evidence. |
| [CVE-2026-84243](https://security-tracker.debian.org/tracker/CVE-2026-84243), glibc | `2.36-9+deb12u14`: vulnerable | `2.41-12+deb13u5`: vulnerable, no DSA/minor issue; no fixed Debian version recorded | Attacker controls `LANGUAGE`, selects a non-C message locale, and supplies a readable malicious `.mo` catalog across an environment trust boundary. Native gettext can crash or misuse format strings. | Application speech-language settings are data, not process-locale changes. No request-to-`LANGUAGE`/`setlocale` path was found. Keep process locale/environment operator-controlled and catalogs read-only. Non-root execution limits privilege but does not protect application secrets after code execution. Track the glibc fix. |
| [CVE-2026-107496](https://security-tracker.debian.org/tracker/CVE-2026-107496), openssl | `3.0.22-1~deb12u2`: vulnerable | `3.5.9-1~deb13u1`: vulnerable/postponed | `CMS_encrypt()` or `_ex()` receives an attacker-selected EC recipient certificate with group order 1 and loops indefinitely. TLS/QUIC do not use this path. | Neither backend nor voice implements CMS recipient-certificate encryption. Fernet recording/contact encryption is a different API. No direct application route found. Keep the library finding open until a Debian fix is installed; upstream rates this **Low**, unlike Scout's HIGH. |
| [CVE-2026-84786](https://security-tracker.debian.org/tracker/CVE-2026-84786), openssl | `3.0.22-1~deb12u2`: vulnerable | `3.5.9-1~deb13u1`: vulnerable/postponed | A client enables OpenSSL DANE and combines PKIX TLSA usages 0/1 with DANE-TA usage 2; crafted certificate/TLSA combinations cause a NULL dereference. | HTTPS use alone does not establish this trigger. No application DANE/TLSA configuration or OpenSSL DANE call was found in either process. Retain the finding and update once fixed. Upstream rates it **Low**; this is a severity disagreement, not a false package match. |
| [CVE-2026-107493](https://security-tracker.debian.org/tracker/CVE-2026-107493), openssl | `3.0.22-1~deb12u2`: vulnerable | `3.5.9-1~deb13u1`: vulnerable/postponed | Caller supplies an existing non-NULL `X509_INFO` stack to the PEM read family; malformed input makes a failed read free entries without removing their pointers, causing later use-after-free. | No such stack API is used by application code. Upstream says OpenSSL's own TLS CA-loading callers pass NULL and avoid this trigger. Native transitive callers were not exhaustively proven absent. Retain/update the library; upstream severity is **Low**. |
| [CVE-2026-112314](https://security-tracker.debian.org/tracker/CVE-2026-112314), krb5 | `1.20.1-2+deb12u5`: vulnerable | Fixed in security `1.21.3-5+deb13u2`; [DSA-6650-1](https://www.debian.org/security/dsa-6650-1) | GSS-RPC TCP client leaks reply verifiers on mismatched transaction IDs or authentication retries, permitting memory exhaustion. | Neither process provides a GSS-RPC client workflow. PostgreSQL URL/password configuration is not evidence of a GSS-RPC call. Verify the affected `libgssrpc` binary separately from generic krb5 source matches; install the fixed Trixie packages where retained. |
| [CVE-2026-112320](https://security-tracker.debian.org/tracker/CVE-2026-112320), krb5 | `1.20.1-2+deb12u5`: vulnerable | Fixed in security `1.21.3-5+deb13u2`; DSA-6650-1 | GSS-RPC server fails to release authentication contexts when a TCP client changes from GSS to another authentication flavor; repeated changes exhaust memory. | No `kadmind` or GSS-RPC server is configured in backend or voice. Verify binary presence and service exposure rather than equating `libgssapi-krb5` with `libgssrpc`. Use fixed packages where retained. |
| [CVE-2026-112321](https://security-tracker.debian.org/tracker/CVE-2026-112321), krb5 | `1.20.1-2+deb12u5`: vulnerable | Fixed in security `1.21.3-5+deb13u2`; DSA-6650-1 | Failed crafted `AUTH_GSSAPI_INIT` requests leave decoded arguments allocated in the GSS-RPC server, exhausting memory. | Neither application exposes this authentication service. No direct web/call path found; a generic krb5 package match is insufficient to prove it. Confirm affected binary absence or install the fixed package. |
| [CVE-2026-112322](https://security-tracker.debian.org/tracker/CVE-2026-112322), krb5 | `1.20.1-2+deb12u5`: vulnerable | Fixed in security `1.21.3-5+deb13u2`; DSA-6650-1 | Many AUTH_GSSAPI client entries cause quadratic CPU use during GSS-RPC client-list cleanup, denying service to `kadmind`. | No Kerberos administrative endpoint is configured. Application resource limits bound container impact but would not prevent this service-level denial of service if the service were added. Keep the service absent and use fixed retained libraries. |
| [CVE-2026-112323](https://security-tracker.debian.org/tracker/CVE-2026-112323), krb5 | `1.20.1-2+deb12u5`: vulnerable | Fixed in security `1.21.3-5+deb13u2`; DSA-6650-1 | Repeated failed AUTH_GSSAPI initialization creates client state retained for 15 minutes, allowing memory growth faster than reclamation. | No GSS-RPC authentication server exists in either application. Verify `libgssrpc`/server binaries and preserve that boundary. MIT's fix disables the obsolete server flavor by default; do not enable it for this application. |
| [CVE-2026-119079](https://security-tracker.debian.org/tracker/CVE-2026-119079), krb5 | `1.20.1-2+deb12u5`: vulnerable | Fixed in security `1.21.3-5+deb13u2`; DSA-6650-1 | ASN.1 `SEQUENCE OF` decoding grows an array one element at a time. Large unauthenticated AS-REQ/TGS-REQ messages over TCP consume excessive KDC CPU. | No KDC is configured. Unlike the RPC-specific findings, the decoder belongs to core libkrb5, so absence of `libgssrpc` alone is not enough. Keep Kerberos authentication unconfigured and use the fixed package; review again if GSS/Kerberos integration is introduced. |
| [CVE-2026-85091](https://security-tracker.debian.org/tracker/CVE-2026-85091), zlib | `1:1.2.13.dfsg-1`: vulnerable | Fixed in `1:1.3.dfsg+really1.3.1-1+deb13u1`; [DSA-6613-1](https://www.debian.org/security/dsa-6613-1) | A failed underlying write leaves a stale input pointer in the native `gz*` writer; a later write can overflow heap memory. This Bookworm version additionally requires `gzclearerr()` after the error. | Recovery reads gzip archives through Python `tarfile`; that is not the native failed-write/retry sequence. No application `gzclearerr` or `gz*` writer call was found. Native voice dependencies require separate inventory review. Use the fixed zlib package, without claiming all gzip processing is unaffected. |
| [CVE-2026-76844](https://security-tracker.debian.org/tracker/CVE-2026-76844), scanner says zlib | Scanner matches `1:1.2.13.dfsg-1`; **wrong product** | No Debian zlib fix applies to this CVE | Official CVE describes path traversal in **webpack-dev-middleware**, using a malformed publicPath prefix and physical-filesystem output. Debian records `NOT-FOR-US: webpack-dev-middleware`. | The reported zlib match contradicts the individual official record. Classify this specific match as a package mismatch and retain it in raw counts. Reporting the mapping defect to the scanner vendor is recommended; no report has been sent. This does not waive the separate genuine zlib CVE above. |
| [CVE-2026-119373](https://security-tracker.debian.org/tracker/CVE-2026-119373), sqlite3 | `3.40.1-2+deb12u2`: vulnerable | `3.46.1-7+deb13u2`: vulnerable, no DSA/minor issue; unstable fixed at `3.54.1-1` | Attacker populates FTS5 `detail=none` with negative rowids and executes a prefix MATCH query, overflowing the merged doclist buffer. | Shared production backend uses PostgreSQL. Neither application accepts arbitrary SQLite databases or exposes FTS5 SQL. SQLite may remain a Python/runtime dependency; retain the finding unless the affected library is demonstrably removed. Track a supported stable fix. |
| [CVE-2026-119374](https://security-tracker.debian.org/tracker/CVE-2026-119374), sqlite3 | Debian labels `3.40.1-2+deb12u2` vulnerable; named feature absent in the baseline probe, as explained below | `3.46.1-7+deb13u2`: vulnerable, no DSA/minor issue; unstable fixed at `3.54.1-1` | Deletion with FTS5 secure-delete enabled processes a corrupted index containing crafted footer offsets and writes beyond a heap buffer. | No imported SQLite database or FTS5 secure-delete workflow exists in backend or voice. Trixie does include the affected feature. PostgreSQL use reduces the application route but does not patch the installed library. Preserve both Debian's status and the baseline feature discrepancy. |
| [CVE-2026-119379](https://security-tracker.debian.org/tracker/CVE-2026-119379), sqlite3 | `3.40.1-2+deb12u2`: vulnerable | `3.46.1-7+deb13u2`: vulnerable, no DSA/minor issue; unstable fixed at `3.54.1-1` | A session-extension changeset repeats a table with a larger column count; applying it overruns an undersized update-mask allocation. | No SQLite changeset import or `sqlite3changeset_apply` route was found in either process. This is distinct from ordinary Django SQL. Preserve the finding while the affected library remains; do not infer safety solely from PostgreSQL being selected. |

## Nine additional HIGH findings from Trivy

These are nine additional CVE identifiers, not nine additional affected
applications. The installed Trixie binary versions below were read from both
final images. Debian source-package status is kept separate from the
presence of the specific affected component. Trivy's remaining 13 unique HIGH
CVEs include these nine plus original `78410`, `119373`, `119374` and `119379`.

| CVE / package | Bookworm comparison | Installed Trixie / Debian status | Trigger, affected component and application assessment |
| --- | --- | --- | --- |
| [CVE-2026-76642](https://security-tracker.debian.org/tracker/CVE-2026-76642), util-linux | **Not affected**: code introduced in 2.39 | `2.41.5-0+deb13u1`: vulnerable; no DSA/minor; unstable fix `2.42.3-1` | A failed mount helper still runs privileged idmap/owner hooks, allowing unauthorized filesystem or inode changes. `mount` was removed from both images, closing that executable path; `libmount1` remains. Neither application provides mount/fstab workflows. Removal is a component mitigation, not a library patch. |
| [CVE-2026-78408](https://security-tracker.debian.org/tracker/CVE-2026-78408), util-linux | **Not affected**: option introduced in 2.40 | `2.41.5-0+deb13u1`: vulnerable; no DSA/minor; unstable fix `2.42.4-1` | A root operator runs `nsenter --join-cgroup` against an attacker-controlled target; a leaked privileged descriptor lets the resulting program move/terminate other processes. `nsenter` remains, mode 0755. No application invocation exists; non-root/capability restrictions reduce exposure. Avoid privileged operator use of this option until patched. |
| [CVE-2026-78409](https://security-tracker.debian.org/tracker/CVE-2026-78409), util-linux | **Not affected** according to Debian | `2.41.5-0+deb13u1`: Debian marks vulnerable; no DSA/minor; unstable fix `2.42.3-1` | With Linux 6.15+ and an authorized `X-mount.subdir` fstab entry, intermediate symlinks escape the intended filesystem. Removed `mount` closes the normal utility path; retained libmount is not declared fixed. The record's note says the code was added in 2.42 despite marking Trixie 2.41.5 vulnerable: retain this tracker inconsistency rather than inferring an unverified backport. |
| [CVE-2026-54369](https://security-tracker.debian.org/tracker/CVE-2026-54369), acl | `2.3.1-3`: vulnerable/postponed | `libacl1` binary `2.3.2-2+b1`, source `2.3.2-2`: vulnerable; point-release update planned; unstable fix `2.4.0-1` | A privileged caller uses pathname ACL APIs on attacker-controlled path components; symlink substitution redirects ACL operations. The new fix adds an ABI, so individual backports are not planned. libacl remains, while ACL command tools are absent. Application files are owned by the non-root service user; no request-driven privileged ACL operation was found. |
| [CVE-2025-69720](https://security-tracker.debian.org/tracker/CVE-2025-69720), ncurses | `6.4-4`: vulnerable/no DSA | `6.5+20250216-2`: vulnerable/no DSA; unstable fix `6.6+20251231-1` | Crafted terminal descriptions trigger a stack overflow in `infocmp` string analysis. `infocmp` is installed, mode 0755. No backend/voice route executes it or accepts terminal descriptions; its presence remains a finding. This CLI bug does not establish that ordinary ncurses library use has the same flaw. |
| [CVE-2026-107494](https://security-tracker.debian.org/tracker/CVE-2026-107494), openssl | **Not affected** in Debian OpenSSL 3.0; backend wheel already bundles 3.5.9 | `3.5.9-1~deb13u1`: vulnerable/postponed; no Debian fix | A malicious OpenSSL QUIC peer repeats connection-ID frames and grows the retire-frame queue without bound. Neither application configures OpenSSL QUIC; backend HTTP/TLS termination and libpq TLS are different paths. Voice native transport has not been exhaustively audited, so this is not a claim that every network dependency is safe. Upstream severity **Low**. |
| [CVE-2026-124709](https://security-tracker.debian.org/tracker/CVE-2026-124709), openssl | **Not affected** in Debian OpenSSL 3.0; affected feature starts at 3.5 | `3.5.9-1~deb13u1`: vulnerable/postponed; no Debian fix | A client establishes an OpenSSL QUIC connection, repeatedly opens/resets rejected streams, and slowly exhausts server memory. No OpenSSL QUIC server is configured in backend or voice. Shared VPS serves HTTP behind host Nginx; no QUIC listener is published by these containers. Retain the library finding; upstream severity **Low**. |
| [CVE-2026-16742](https://security-tracker.debian.org/tracker/CVE-2026-16742), systemd | `252.39-1~deb12u2`: vulnerable/postponed | `libsystemd0`/`libudev1` `257.13-1~deb13u1`: source vulnerable/no DSA; unstable fix `261.2-1` | A logged-in homed-managed user adds privileged system groups through systemd-homed. Both images contain systemd libraries, but the `systemd-homed` service/binary and homed users are absent. This particular service path is absent; the source-package scanner match stays visible. |
| [CVE-2026-9538](https://security-tracker.debian.org/tracker/CVE-2026-9538), perl | `5.36.0-7+deb12u4`: vulnerable/postponed | `perl-base` `5.40.1-6+deb13u1`: source vulnerable/postponed; unstable fix `5.42.3-1` | Perl `Archive::Tar` before 3.10 allocates an unbounded scalar from an attacker-controlled archive entry size. Only perl-base is installed, and the affected module is absent in both images. Recording restore uses Python tarfile with explicit size/path/type checks, not this Perl module. Do not extrapolate that distinction to every archive implementation. |

## Two baseline CRITICAL matches

Trivy reports [CVE-2025-7458](https://security-tracker.debian.org/tracker/CVE-2025-7458)
against baseline SQLite `3.40.1-2+deb12u2`. A caller able to execute arbitrary SQL
can overflow an allocation through a large ORDER BY expression list. Debian
records this as fixed in the installed Trixie `3.46.1-7+deb13u2` (upstream fix
was included by Debian `3.42.0-1`). This is an actual version fix.

The other CRITICAL match,
[CVE-2023-45853](https://security-tracker.debian.org/tracker/CVE-2023-45853),
is against baseline `zlib1g`. It concerns MiniZip's ZIP-writing routine, not
ordinary zlib compression. Debian explicitly records that the affected
`contrib/minizip` code was not built into Bookworm's zlib binary packages.
The Trixie source version is recorded fixed, and no MiniZip component was found
in either final image. Keep the baseline raw CRITICAL match, but do not
present it as demonstrated exposure in `zlib1g` or as a newly removed executable.

## Native libraries and feature checks

Debian package inventories alone do not cover wheel-bundled or statically linked
libraries. The backend's `psycopg-binary 3.3.9` loads bundled `libkrb5`,
`libgssapi_krb5`, `libk5crypto`, `libkrb5support`, `libssl` and `libcrypto`.
All six files have identical SHA-256 hashes in the baseline and final
backend. `/proc/self/maps` confirms loading, and the bundled OpenSSL runtime
reports **3.5.9**. Upgrading Debian does not patch these copies.

The backend's `cryptography 50.0.2` separately reports statically linked
**OpenSSL 4.0.3**, also before the upcoming 4.0.4 advisory fixes. Python-package
scanners returning zero findings do not clear these embedded native libraries.
Fernet operations do not use the documented CMS, DANE, PEM-stack or QUIC APIs;
that narrows the application route without claiming the library is patched.

The [upstream 3.3.9 build recipe](https://github.com/psycopg/psycopg/blob/3.3.9/tools/ci/build_libpq.sh)
selects **Kerberos 1.22.2**, before the reviewed upstream fixes in 1.22.3.
This is source-recipe evidence; the stripped loaded libraries did not expose a
release string and have not been independently attested to that build. Their
fixed status must not be assumed. The same recipe disables libpq's default
GSS encryption, and a runtime defaults query confirms `gssencmode=disable`.
No KDC, kadmind, or libgssrpc component was found. These facts narrow the known
server/RPC triggers but do not establish that every core ASN.1 decoder path is
unreachable. A future wheel update needs its own native-library review. The
separate voice image does not install psycopg; its other native extensions still
require their own upstream maintenance and cannot be cleared by Debian scans.

Both tested SQLite builds enable FTS5 and the session extension. A harmless
in-memory feature probe rejects FTS5 `secure-delete` configuration on baseline
SQLite **3.40.1** and accepts it on Trixie **3.46.1**. SQLite's
[3.42.0 release notes](https://www.sqlite.org/releaselog/3_42_0.html) identify
that release as the introduction of the
[FTS5 secure-delete option](https://www.sqlite.org/fts5.html#the_secure_delete_configuration_option).
Thus the specific named feature in CVE-2026-119374 is absent from this baseline,
despite Debian's broad vulnerable label. This bounded discrepancy is documented
without overriding or suppressing the tracker result. The newer image includes
the feature and remains unpatched. No malformed database or exploit was run.

The tested image locale is `LANG=C.UTF-8`, with no `LANGUAGE` override; Python's
active `LC_MESSAGES` reports `C`, and only C/C.utf8/POSIX locales are installed.
This avoids the documented gettext locale prerequisite in the tested process.
Operator-provided runtime environment changes can invalidate that observation.

## Advisory details and mitigation limits

The five OpenSSL CVEs reviewed above have upstream severity **Low**, and their
three advisory publications
say fixed releases are scheduled for **1 December 2026**. They name `3.5.10` for
the 3.5 line and `3.0.24` for premium-support 3.0 customers. Public upstream fixes
do not mean those versions are already available from Debian. Recheck Debian's
package status after that date; do not replace distribution libraries with an
unreviewed source build solely to change a scanner count.
[10 November advisory](https://openssl-library.org/news/secadv/20261110.txt),
[17 November advisory](https://openssl-library.org/news/secadv/20261117.txt),
[24 November advisory](https://openssl-library.org/news/secadv/20261124.txt).

The Kerberos fixes are also described by
[MITKRB5-SA-2026-001](https://web.mit.edu/kerberos/advisories/MITKRB5-SA-2026-001.txt).
The source package includes clients, core libraries, GSS-RPC and servers; these
must be inventoried separately. None of the six rows establishes that an
application using password-authenticated PostgreSQL exposes a Kerberos server.

The glibc advisory explicitly requires environment and catalog control together
with an applicable non-C locale. A C message locale avoids the documented route;
read-only catalogs and refusing untrusted locale environment variables are
additional controls. Application UI/speech language settings are not evidence of
that operating-system locale configuration.
[GLIBC-SA-2026-0025](https://sourceware.org/git/?p=glibc.git;a=blob_plain;f=advisories/GLIBC-SA-2026-0025).

Shared backend services use a non-root image user, drop all capabilities, set
`no-new-privileges`, and mount the root filesystem read-only; the one-off migration
service needs writable application files. These settings mitigate specific local
mount/privilege paths and constrain impact. They do not stop all memory corruption,
protect secrets from compromised application code, or make an unpatched library
fixed. The voice Dockerfile's non-root user is verifiable locally, but LiveKit
Cloud runtime capabilities and mount policy have not been inspected in this
review. Host Nginx, PostgreSQL/Redis images and the Docker host kernel are separate
scan scopes.

## Base choice and support window

[Debian Trixie](https://www.debian.org/releases/trixie/) has full Debian support
until **9 August 2028**, followed by LTS until **30 June 2030**.
[Bookworm](https://www.debian.org/releases/bookworm/) has entered LTS, which
ends **30 June 2028**; its full-support period ended on 11 July 2026. LTS supports
a reduced architecture set. Trixie offers a longer maintained lifecycle and
several actual fixes, while the package-level feature tradeoffs above remain part of the
decision. Neither release promises an immediate fix for every low-priority
Debian finding.

Alpine is not a supported substitute for the LiveKit Cloud image:
[LiveKit's build documentation](https://docs.livekit.io/deploy/agents/builds/)
requires a glibc-based image and explicitly excludes Alpine/musl. Debian Trixie
meets that libc-family requirement; actual worker/native compatibility still
needs offline regression checks. A smaller scanner count alone is not grounds
to switch libc families or delete Essential packages.

## Scanner comparison and remaining findings

The following are raw results from the final immutable rebuild. Counts from
different scanners use different package matching and severity feeds; they
cannot be subtracted from one another. The Trivy comparisons use the same
scanner/database run and explicitly deduplicate CVE identifiers for the summary.

| Scanner / scope | Bookworm baseline | Final Trixie backend and voice |
| --- | --- | --- |
| Docker Scout, OS findings | 100: 18 HIGH, 23 MEDIUM, 50 LOW, 9 UNSPECIFIED | 27: 0 HIGH/CRITICAL, 1 MEDIUM, 26 LOW; same in backend and voice |
| Trivy 0.76.2, Debian binary matches | 374 matches | 213 matches |
| Trivy, distinct Debian CVEs | 170: 2 CRITICAL, 29 HIGH, 64 MEDIUM, 60 LOW, 15 UNKNOWN | 99: 0 CRITICAL, 13 HIGH, 36 MEDIUM, 39 LOW, 11 UNKNOWN |

**The Scout result is a feed-coverage discrepancy, not proof that Debian's
remaining findings disappeared.** All eight original source-package records
remain unresolved in the retained Trixie versions. Removing `mount` eliminates
that CLI entrypoint but does not patch or remove retained `libmount1`. The backend also
retains the separately bundled native libraries discussed above. Trivy finds
additional HIGH source matches, including five CVEs Debian marks not affected
in Bookworm's OS packages. This does not establish that all five were absent
from the baseline image's bundled libraries. Missing components, unchanged
vulnerable libraries, actual fixes and
scanner classification disagreements are separate outcomes.

The final images remove seven nonessential packages through apt:
`adduser`, `passwd`, `mount`, `libbsd0`, `libsemanage-common`, `libsemanage2` and
`libsepol2`. They retain Essential packages and package inventory metadata.
Available Trixie updates include `libzstd1 1.5.7+dfsg-1+deb13u1` and
`login.defs 1:4.17.4-2+deb13u1`. The experimental scans also identified six
findings in the base image's unused global pip. Final images remove that pip and
the unused CPython bootstrap wheel; both final Trivy Python-package scans report
zero findings. These are separate from the Debian-only counts above and do not
resolve the embedded OpenSSL caveat.

[OS_SECURITY_FINDINGS.json](OS_SECURITY_FINDINGS.json) records every remaining
scanner CVE, including lower severities, installed package versions, raw report
hashes and image identities. Its scanner-provided status is distinct from this
manual official-record assessment. Neither artifact is a suppression list.

## Evidence and final-image verification

Raw baseline SARIF, all 29 individual CVE HTML responses, ten package-tracker
responses, three DSA tracker records, three corresponding Debian publications and
upstream advisory responses are preserved outside the repository. Each official
response has its URL, retrieval time and SHA-256 in a manifest. The evidence set
is named `vaani-security-hardening-20261127/cve-review`; it contains no production
configuration. No scanner suppression or severity override was added.

The final images were built from immutable source commit
`67552f18393a` and checked on 27 November 2026:

| Image | Local immutable image ID |
| --- | --- |
| `vaani-security-final-backend:67552f18393a-20261127` | `sha256:087cebe8e371246b16c04b6216a5ab1fdf7a4ddd5c31610aa01069be98bb36d5` |
| `vaani-security-final-voice:67552f18393a-20261127` | `sha256:e03af7d41f87ade8c5bb53447c1d8500eff29ddaeb40b2de8d78def6e6f93210` |

Both final images contain 80 installed Debian packages and run as UID 10001.
Final component probes confirm the executable/module absences recorded above;
feature probes confirm SQLite and locale observations in both images and the
backend's native crypto versions. Probes used isolated, network-disabled,
read-only containers and valid in-memory data. The raw final Trivy report
SHA-256 values are
`2cb7e83b23272ca3e68a9e736897ea08ae7f4c9c05589df6dcd2457cfc101f28`
(backend) and
`fb65610efcca105f0411912f2ba2d4ecc6a581ccfb987da6cc0195f5ec39e8c1`
(voice).

This is a **conditional hardening review, not a clean production-security
approval**. Remaining Debian and bundled-library findings need continued
tracking and supported updates. Offline regression/runtime checks are recorded
separately in [PR_REVIEW.md](PR_REVIEW.md); they do not establish that
all vulnerabilities are unexploitable. Source searches support only the bounded
route assessments above. No exploit, real call, Cloud-worker start or production
deployment was performed for this review.
