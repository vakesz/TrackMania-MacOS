# Public TLS test material

Every key in this directory is PUBLIC TEST DATA, not a secret. Never use these
certificates or keys outside a disposable test environment. Do not install either
CA in macOS Keychain, a Windows installation, or your game/default Wine prefix.
The CA signing keys are discarded during generation. `server.key` is deliberately
public and shared by the eight leaf certificates and the legacy self-signed one.

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
| `legacy-self-signed.pem` | itself | `CN=127.0.0.1`, IP and DNS SAN `127.0.0.1` | 2020-2021 |

`legacy-self-signed.pem` is not part of the case matrix. Only the TLS 1.0 legacy
mode serves it (`tls_test_server.py --legacy-tls10`). It mirrors Nadeo's
self-signed, expired account server certificates: a client sees an untrusted root
(12045) and not a revocation failure (12057), which a leaf from an unsent private
CA would give. `tests/inet_legacy.c` uses it for the account creation regression.

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
python3 src/python/trackmania_mac/probes/tls_test_server.py
```

The server prints one JSON object containing a URL for each leaf listed above,
on separate ephemeral ports. It stays running until SIGINT, SIGTERM, or
SIGHUP. Python tests use a private OpenSSL trust context containing only `ca.pem`;
they never run Wine or modify a certificate store. Serving and testing committed
fixtures requires Python 3.9 or newer, with no dependencies or external network.

Regenerate the public test material with OpenSSL 1.1.1 or newer:

```sh
python3 src/python/trackmania_mac/probes/tls_test_server.py --generate-fixtures tests/fixtures/tls
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

- A 32-128 hexadecimal character nonce matching `TRACKMANIA_MAC_DISPOSABLE_TEST`.
- The exact file `C:\trackmania-mac-test-prefix.txt` containing precisely that nonce.
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

## Known gaps

The suite does not cover:

- Closing or retrying a request from its callback, and cancelling a pending
  async request.
- A forced `CertVerifyCertificateChainPolicy` API failure and allocation
  failures.
- Revoked certificates and online revocation (the leaves carry no CRL or OCSP
  URLs), wrong-usage certificates, and signature or critical-extension failures.
- Resending on a retained socket within the same request, proxy tunnels and
  client-certificate pool identity.
- Comparison against native Windows.
- Qualification of a complete runtime or release package. The checks run
  against isolated DLLs and fresh owned prefixes.
