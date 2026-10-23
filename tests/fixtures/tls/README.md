# Public TLS test material

Every key in this directory is PUBLIC TEST DATA, not a secret. Never use these
certificates or keys outside a disposable test environment. Do not install either
CA in macOS Keychain, a Windows installation, or your game/default Wine prefix.
The CA signing keys are discarded during generation. `server.key` is deliberately
public and shared by the eight leaf certificates.

| File | Issuer | Identity | Validity |
| --- | --- | --- | --- |
| `valid.pem` | `ca.pem` | IP and DNS SAN `127.0.0.1` | 2020-2040 |
| `untrusted.pem` | `untrusted-ca.pem` | IP and DNS SAN `127.0.0.1` | 2020-2040 |
| `expired.pem` | `ca.pem` | IP and DNS SAN `127.0.0.1` | 2020-2021 |
| `hostname.pem` | `ca.pem` | DNS SAN `wrong-host.invalid` | 2020-2040 |
| `expired-untrusted.pem` | `untrusted-ca.pem` | IP and DNS SAN `127.0.0.1` | 2020-2021 |
| `hostname-untrusted.pem` | `untrusted-ca.pem` | DNS SAN `wrong-host.invalid` | 2020-2040 |
| `expired-hostname.pem` | `ca.pem` | DNS SAN `wrong-host.invalid` | 2020-2021 |
| `expired-hostname-untrusted.pem` | `untrusted-ca.pem` | DNS SAN `wrong-host.invalid` | 2020-2021 |

The roots are valid from 2019 to 2041, covering every leaf's entire validity
period. Tests fail if the clock is outside the valid control's validity window.
No certificate contains AIA, CRL, or OCSP URLs. The server sends only the leaf
certificate and listens on IPv4 loopback. The additional numeric DNS SAN lets
Wine versions with DNS-only SSL name matching validate the positive control.
Python still checks the IP SAN. This suite does not qualify Wine's IP SAN support.

## Offline checks

From the repository root:

```sh
python3 -m unittest discover -s tests -p test_network.py
python3 src/python/tmnf/probes/tls_test_server.py
```

The server prints one JSON object containing a URL for each leaf listed above,
on separate ephemeral ports. It stays running until SIGINT, SIGTERM, or
SIGHUP. Python tests use a private OpenSSL trust context containing only `ca.pem`;
they never run Wine or modify a certificate store. Serving and testing committed
fixtures requires Python 3.9 or newer, with no dependencies or external network.

Regenerate the public test material with OpenSSL 1.1.1 or newer:

```sh
python3 src/python/tmnf/probes/tls_test_server.py --generate-fixtures tests/fixtures/tls
```

Regeneration replaces both roots and all leaves. An old imported root will no
longer validate the regenerated control. Never keep a prefix across regeneration.

### Sync and async request modes

The runtime suite runs every case in both modes on both architectures. The
native CLI accepts `network.exe [--async] --local CONTROL_URL CASE_URL EXPECTED_KIND`
with optional `INITIAL_FLAGS FINAL_FLAGS` numeric arguments. It sets both values
in order on the request and queries the effective flags before sending. This
checks replacement, not accumulation, including a reset to zero.
Without `--async`, local requests stay synchronous; no arguments still select
the existing synchronous CDN check. Async mode is local-only. The runtime suite
invokes the executable directly. The additional policy and pool arguments are
not part of the smoke wrapper's argument contract.

Async requests open WinHTTP with `WINHTTP_FLAG_ASYNC`. A per-request context is
installed with `WINHTTP_OPTION_CONTEXT_VALUE` and passed to `WinHttpSendRequest`.
The callback validates the supplied context, request handle, notification mask,
and payload sizes, including exactly `sizeof(DWORD)` for secure failures and
`sizeof(WINHTTP_ASYNC_RESULT)` for request errors. Counts are per request, not
global totals shared between the trusted control and the rejection case.

The event-driven success sequence is one `SENDREQUEST_COMPLETE`, followed by
the main thread's `WinHttpReceiveResponse` call, then one `HEADERS_AVAILABLE`.
A negative must deliver exactly one `SECURE_FAILURE` with the intended bit,
then exactly one `REQUEST_ERROR` containing `API_SEND_REQUEST` and error `12175`.
It must not deliver send-complete or headers-available notifications. Synchronous
negatives still require exactly one secure-failure notification. The original
controls and single-error cases set no ignores. The policy matrix separately
tests explicit application ignore options against single and combined errors.

Each async progress wait is bounded to 10 seconds. After `WinHttpCloseHandle`,
the probe waits up to five seconds for exactly one `HANDLE_CLOSING` notification
before releasing its context or events. Signaling that final event is the
callback's last access to the context. A missing closing notification exits the
process without unwinding callback-owned state, instead of risking a use-after-free.
The host runner's existing deadlines and owned-prefix cleanup still apply.

The callback summary reports counts, validation errors, and sequence positions
in `secure/send/receive/headers/request-error/closing` order. Async success is
`0/1/2/3/0/4`; rejection is `1/0/0/0/2/3`. These assertions execute against actual
WinHTTP callbacks. Offline tests cover argument configuration and compilation,
not simulated protocol success.

`tests/tls-trust.c` accepts a PEM path, nonce, and expected SHA-256 of the decoded
CA. Before opening any certificate store it requires all of the following:

- A 32-128 hexadecimal character nonce matching `MACTMNF_DISPOSABLE_TEST`.
- The exact file `C:\mactmnf-test-prefix.txt` containing precisely that nonce.
- A file no larger than 64 KiB whose decoded certificate matches the runner's
  pin of `tests/fixtures/tls/ca.pem`.
- The test CA's exact common name, self-issued name and valid self-signature,
  CA basic constraints with path length zero, and certificate-signing key usage.

The runner creates the marker after verifying the prefix's C: mapping. The
importer holds the marker open without write/delete sharing until it finishes.
It adds only the pinned CA to CurrentUser ROOT with CryptoAPI and no UI. It
never changes macOS Keychain. PEM decoding uses `CRYPT_STRING_BASE64HEADER`;
the installed Wine rejects `CRYPT_STRING_STRICT`, and the SHA-256 pin verifies
the exact decoded certificate independently of permissive PEM formatting.

The suite checks missing/mismatched markers and environment values, short and
non-hexadecimal nonces, incorrect pins, the wrong CA, a leaf certificate, and an
oversized input.
Every refusal must occur before the store-open diagnostic. The valid endpoint
must fail before those attempts, fail again afterward, then succeed after the
authorized import. Both architectures run all eight TLS cases in sync and async
modes. Every negative
invocation requires its own successful positive control and exactly
`ERROR_WINHTTP_SECURE_FAILURE` with exactly the requested secure-failure flag.
Timeouts, successful negative requests, missing callbacks, and extra flags fail.

Cleanup validates owned prefix paths and directory identities, calls only the
selected runtime's prefix-scoped `wineserver -k` and `-w` with 15-second deadlines,
and deletes the scratch directory only after successful waits. Failed cleanup
retains and reports the scratch path. SIGINT, SIGTERM and SIGHUP run cleanup;
an additional runtime test sends SIGTERM after a real CA import and requires
cleanup to finish. SIGKILL cannot run cleanup and may leave scratch processes
and files, but no trusted root is left in a user prefix or macOS Keychain.
Concurrent suites own different directories and never kill each other's servers.

## Observed runtime 0.2.0 results

The installed `.build/release/runtime` was exercised on 2026-09-07. Both PE
architectures passed importer guards, rejected the control before import, and
returned HTTP 200 after import. All three negative cases were rejected, but
failed the strict WinHTTP error/callback contract:

| Case | Internal certificate error | Returned error | Callback flags | Required flag |
| --- | --- | --- | --- | --- |
| Untrusted | 12045, invalid CA | 12157 | 0 | `0x08`, invalid CA |
| Expired | 12037, date invalid | 12157 | 0 | `0x20`, date invalid |
| Hostname | 12038, CN invalid | 12157 | 0 | `0x10`, CN invalid |

The bundled Wine's `dlls/winhttp/net.c`, `netconn_secure_connect`, detects the
individual certificate errors but returns `ERROR_WINHTTP_SECURE_CHANNEL_ERROR`
at its common failure exit. It does not report the required secure-failure
callback. WinHTTP warnings capture the internal reasons for diagnosis; they are
not accepted as substitutes for the API contract. The suite therefore remains
red on that runtime, with six negative-case failures, not weakened expectations
or expected-failure annotations. Fix and rebuild that backend separately, then
rerun this suite. These results are not release qualification for 0.3.0 or for
the unbuilt current sources.

## Observed isolated patch 0010 results

On 2026-09-07, both runtime tests passed with the installed runtime 0.2.0 and
the native DLL overlay at the approved temporary `winhttp-isolated-0010/native`
directory. The DLLs were built from recorded Wine revision
`36b6a2cf679fb395f668a917b76537190e212d9c` and patch SHA-256
`aae3034d7633423e53d4e530cd3573cd0e2d6b5673036f15a44801358a2325c5`.

| Architecture | Native DLL SHA-256 |
| --- | --- |
| i686 | `7f2cd216e15c98c286b52749d6745c6721f5a1d85cb2febe6e35fa06ee4bd9b2` |
| x86_64 | `eef3dd09e55961efe50cd4891df198033a987bf1329d868f9259247d3a1803a2` |

Both architectures reported app-local module paths under their own scratch
`bin-ARCH` directories. Missing/mismatched ownership proofs and invalid CA
inputs were refused before opening the store. The valid endpoint failed before
import and returned HTTP 200 afterward. Each negative case passed its own
trusted positive control and then met the strict API contract:

| Case | Returned error | Callback flags | i686 | x86_64 |
| --- | --- | --- | --- | --- |
| Untrusted | 12175 | `0x08` | Pass | Pass |
| Expired | 12175 | `0x20` | Pass | Pass |
| Hostname | 12175 | `0x10` | Pass | Pass |

SIGTERM after an actual CA import also passed cleanup. Owned wineservers were
stopped and scratch directories removed. The six failures in the preceding
section describe the original, unmodified 0.2.0 WinHTTP DLLs; they were not
reclassified or hidden. These green overlay results cover only the synchronous
local TLS cases and importer/cleanup checks. They do not qualify a complete
0.3.0 runtime, a release package, async/reentrant callbacks, or allocation-failure
paths. No installed runtime DLL was replaced.

## Observed verified-overlay async results

On 2026-09-07, the expanded sync/async suite passed against runtime 0.2.0 with
`winhttp-isolated-0010-verified/native`. The recorded Wine revision is
`36b6a2cf679fb395f668a917b76537190e212d9c`, with patch SHA-256
`0ab16654d8a8cbca7511b97555510ec0bcd9464b74a49d9998af6355431eab3c`.
These artifacts precede the latest source-patch header and warning-log changes;
this result does not claim to test those subsequent rebuilds.

| Architecture | Tested native DLL SHA-256 |
| --- | --- |
| i686 | `bd1b3c755a0a2dee468fededf44358ca1d1a341db0ab5b99d021700fc2c2fd5a` |
| x86_64 | `526450d36b6e583a97492b1d0911507ded6a48fc3626fefdfce8b74f13e7c84c` |

| Mode and architecture | HTTP 200 control | Untrusted `0x08` | Expired `0x20` | Hostname `0x10` |
| --- | --- | --- | --- | --- |
| i686 sync | Pass | Pass | Pass | Pass |
| i686 async | Pass | Pass | Pass | Pass |
| x86_64 sync | Pass | Pass | Pass | Pass |
| x86_64 async | Pass | Pass | Pass | Pass |

Every async negative reported `secure=1`, `send=0`, `headers=0`, `errors=1`,
`closing=1`, `bad=0`, order `1/0/0/0/2/3`, `async_error=12175`, and `api=5`.
Both architectures passed the context, handle, mask, and size checks. App-local
module proofs, both-mode missing-root controls, importer guards, and SIGTERM
cleanup after an actual CA import also passed. Scratch prefixes were removed.

Still unverified: behavior with no secure-failure subscription, closing or
retrying a request from its callback, allocation-failure paths, and comparison
against native Windows. The SIGTERM test verifies host cleanup after import;
it does not deliberately cancel a pending async request. None of these results
qualifies a complete 0.3.0 runtime or release package.

## Observed isolated patch 0011 results

On 2026-09-07, all fixtures were regenerated together using
`src/python/tmnf/probes/tls_test_server.py --generate-fixtures tests/fixtures/tls`. Both CA signing
keys were discarded. No old imported root was reused. Offline tests independently
check the eight leaves' issuers, names, validity windows, serverAuth signatures
at a fixed in-range time, and absence of network retrieval extensions.

The red run used runtime 0.2.0 with `winhttp-0010-qualified-overlay/native` under
the approved temporary directory. `0010` was not changed. Its SHA-256 is
`4446a5bcb737ad6879ad343bc84f37f64022074fedf955165a566fd28fdd6641`.
The completed run took 420.863 seconds and reported 28 failures, seven for each
architecture/mode combination:

| Case | Explicit final ignores | Observed on 0010 | Required |
| --- | --- | --- | --- |
| Expired + untrusted | Date | HTTP 200 | Reject invalid CA |
| Expired + untrusted | Date + name | HTTP 200 | Reject invalid CA |
| Expired + hostname + untrusted | Date | Reject hostname | Reject invalid CA in existing WinHTTP priority |
| Expired + hostname + untrusted | Date + name | HTTP 200 | Reject invalid CA |
| Pooled untrusted | CA reset to zero | HTTP 200 | Revalidate and reject CA |
| Pooled expired + untrusted | Date + CA replaced with date | HTTP 200 | Revalidate and reject CA |
| Pooled hostname | Name replaced with date | HTTP 200 | Revalidate and reject name |

Each pool test first made two successful requests with the same permissive
policy and required their `X-Test-Connection` IDs to match. The probe reads to
end-of-response before closing each request to return the socket to the pool.
Thus the unsafe third success was not inferred from timing or debug messages.
An initial shorter red invocation hit its outer timeout and cleaned up; only
the subsequent complete run supplies the counts above.

The green build is `winhttp-0011-correctness-v3` under
`/var/folders/0j/wvhpysw515n21xxh9y6f3k9m0000gn/T/opencode`. It applies unchanged
`0010` followed by `0011`, whose SHA-256 is
`6bf60722c3b8237d7bd1b60b92b1e01118de99ec5f9104be60c50ffc7af0c538`.

| Architecture | Tested native DLL SHA-256 |
| --- | --- |
| i686 | `d6e930e104b062cfef05a2b1d1cf4c1ca353e1137e72b9c423081614df800280` |
| x86_64 | `bb0b990ae8c92dc277d3ed4c4a3bb4fe9f8006c34bcc825814a9a19f4040d235` |

Both runtime tests passed in 493.548 seconds with that overlay over runtime
0.2.0. Every architecture/mode ran the original controls and negatives, 72
fixture/ignore combinations, four flag-change pool cases, one revocation-change
pool case, and three no-secure-subscription cases. The 72 combinations cover
every subset of date/CA/name ignores plus an unrelated wrong-usage ignore for
each leaf. Every request first replaces `0x3300` with its tested final value.
An explicitly ignored category can succeed only when no other error remains.

Equal-policy requests reuse the same connection ID. Changing flags on a valid
endpoint must change the connection ID. Narrowing or resetting ignores on bad
endpoints rejects with `12175` and the remaining category. Enabling revocation
after verified pool reuse rejects the otherwise-valid fixture with flag `0x01`,
because these leaves intentionally have no revocation information. That test
checks policy isolation and revocation-unavailable behavior, not online CRLs or
OCSP. No default revocation option changes.

Without a secure-failure subscription, all three single-error cases still
return `12175` with zero secure callbacks. Async requests deliver one terminal
`REQUEST_ERROR`, then `HANDLE_CLOSING`, in order `0/0/0/0/1/2`. With subscription,
the existing exact callback count, handle, context, payload and order checks
remain unchanged. Importer refusal checks, absent-root controls, module-path
proof and SIGTERM cleanup after import passed. All owned prefixes were removed.

The validator retains WinHTTP's trust checks because pinned Crypt32 SSL policy
does not reject partial chains or all unsupported trust bits. It masks only
explicitly ignored date, CA and usage categories in a local DWORD; it never
clears or edits the original chain. Crypt32 receives that original chain and
the documented SSL ignore flags for its additional checks, including hostname.
Policy status is initialized with its size, and API failure rejects the cert.
See Microsoft's [HTTPSPolicyCallbackData documentation](https://learn.microsoft.com/en-us/windows/win32/api/wincrypt/ns-wincrypt-httpspolicycallbackdata).

Remaining runtime gaps: forced `CertVerifyCertificateChainPolicy` API failure,
allocation failures, revoked certificates and online revocation, wrong-usage
certificates, signature/critical-extension failures, same-request retained-socket
resends, callback-driven close/retry/cancel, proxy tunnels and client-certificate
pool identity, and native Windows comparison. Source inspection covers the
retained-socket policy guard, but the controlled pool tests use new request
handles. The patch reports the first remaining WinHTTP category, not a union of
all certificate errors. These results do not qualify a complete runtime or a
release package. No live prefix, Keychain, installed runtime, prepared source,
or existing Wine work tree was modified.

### Final certificate lookup ownership follow-up

The `v4` update adds one Wine statement to the final certificate-lookup failure
branch in `open_connection`: `request->netconn = NULL` before
`netconn_release(netconn)`. Both the new-connection and pool-reuse paths already
assign that pointer. Leaving it set after release lets `close_connection` release
it again during request cleanup. Comparing the complete `v3` and `v4` WinHTTP
source directories confirmed this assignment is their only source difference.
All preceding validation and pool-policy changes remain intact; `0010` is unchanged.

The new normal build is `winhttp-0011-correctness-v4` under the same approved
temporary directory. All eight C sources were rebuilt for both architectures.
Both patch application checks, compilation, native/builtin PE marker checks,
architecture checks, and the unchanged 49-export tables passed. The new `0011`
SHA-256 is `5bb857d67463a13df88309e4c197a4900aba54fe9f3ee1ecbf4e35b3a290b1bb`.

| Architecture | v4 normal native DLL SHA-256 |
| --- | --- |
| i686 | `080a6090d26b2e319bcd966fdd213257d04d7a5a05a7e5b1d580e5f0373275f1` |
| x86_64 | `d9bfabb7250e293cee1aed5add1bb7b42817dc007da2fd9b0be59014d042a635` |

`src/python/tmnf/build/winhttp_probe_builder.py --fault-base BASE --output NEW_DIRECTORY` builds
separate, explicitly instrumented test DLLs. It reuses the base's recorded
compilation and native-link recipes, compiling one wrapper object with Wine's
headers and placing it before the import libraries. It links with
`--wrap=netconn_get_certificate` and `--wrap=netconn_release`. These wrappers are
not linked into the normal DLLs and add no production hooks or source changes.
The fault manifest records the base path and manifest hash, wrapper hash,
commands and output DLL hashes. Existing directories, instrumented bases,
incorrect revisions, changed Makefiles/DLLs and unrecognized recipes are refused.

The wrapper allows the first certificate lookup, which supplies a real trusted
HTTP 200 control. It returns NULL on the second lookup after the second TLS
handshake. It performs the real connection release once, then exits with code
86 if request cleanup attempts a second release of that pointer, without
dereferencing freed memory. The test uses the existing no-subscription rejection
contract on the valid fixture: `12175`, zero secure callbacks, normal request
closing, and the existing async terminal-error checks. It also requires exactly
one injected lookup failure and exactly one release of the rejected connection.

The red run used `winhttp-0011-v3-lookup-fault-linked/native`. All four
architecture/mode cases reached `release=2` and exited 86. The focused suite
reported four failures in 74.987 seconds. This is an executed cleanup regression,
not a source-text assertion or a crash inferred from an unrelated failure.

The green run used normal `winhttp-0011-correctness-v4/native` DLLs for the full
certificate/pool matrix and separate `winhttp-0011-v4-lookup-fault-linked/native`
DLLs in `fault-bin-ARCH` directories for the injected cases. All four injected
cases passed with one release and completed handle closing. The complete two-test
runtime suite passed in 387.584 seconds, including the existing 288 fixture/flag
combinations, 20 pool sequences, 12 no-subscription checks, importer proofs,
app-local module proofs and SIGTERM cleanup after import. Its captured output is
`/Users/vakesz/.local/share/opencode/tool-output/tool_07c58a06b001y3UMTO1auu4Eq4`.

The injection proves cleanup after `netconn_get_certificate` returns NULL; it
does not force an underlying SSPI allocation failure or a Crypt32 policy-API
failure. Other previously listed gaps remain open. The normal `v3` artifacts and
their historical results remain unchanged. All tests used fresh owned prefixes
and removed them after stopping their wineservers. Prepared sources, existing
Wine work/runtime files, live prefixes and Keychain were not modified. This
follow-up is still isolated DLL testing, not full-runtime release qualification.
