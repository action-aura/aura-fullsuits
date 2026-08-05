# External API Configuration Contract (M9.5)

`ExternalApiConfiguration` (`shared/.../licensing/transport/
ExternalApiConfiguration.kt`) — real, versioned, validated
configuration shape for a future transport implementation.

## Real fields

`environment` (`DEVELOPMENT`/`STAGING`/`PRODUCTION`), `baseUrl`,
`contractVersion` (default `"v1"`, matching every real M7 schema's own
`contract_version` field), `requestTimeoutMillis`/
`connectTimeoutMillis`/`responseTimeoutMillis`, `releaseChannel`,
`productCode`, `platform`, `safeDiagnosticMode`.

## Real, enforced rejections (`validate()`)

- No recognizable scheme → rejected.
- `http` scheme with `environment == PRODUCTION` → rejected
  unconditionally (cleartext production is never permitted).
- `http` scheme outside production → only permitted for an explicit
  local-development host (`localhost`/`127.0.0.1`/`10.0.2.2` — the
  real Android-emulator-to-host-loopback address), never a bare
  cleartext exception.
- Any scheme other than `http`/`https` → rejected.
- Embedded credentials (`@` in the URL) → rejected.
- URL fragment (`#`) → rejected.
- Query string (`?`) → rejected.
- A secret-shaped substring (`license_key`/`serial`/`token`/
  `password`, case-insensitive) anywhere in the base URL → rejected.
- Non-positive timeout → rejected.

No hard-coded production domain, IP address, or speculative Owner
route exists anywhere in `commonMain` — every real value is supplied
by the caller at construction time; this file only validates shape.

## No hard-coded localhost/production values in this repository

Grepped `shared/src/commonMain/` for literal `http://`/`https://` —
zero matches outside this file's own regex patterns (which match
against a caller-supplied string, they do not embed one).
