# Mobile Release / Version Contract (M7.14)

Real Product/Platform/version/channel/min-version/required-update/
checksum/publication/withdrawal representation.

## Real models

- `ReleaseChannel` (`app/models/catalog.py:48-52`,
  `owner_release_channels`): `channel_code` (real values per comment:
  `STABLE`/`RC`/`PILOT`/`BETA`), `name`.
- `ProductVersion` (`app/models/catalog.py:55-78`,
  `owner_product_versions`): `product_id`, `platform_id`,
  `release_channel_id`, `version` (free string, e.g.
  `"1.0.0-rc.1"`), `schema_version`, `financial_contract_version`,
  `artifact_checksum_sha256` (String(64), real SHA-256 checksum),
  `artifact_path`, `release_notes`, `is_current_stable` (bool),
  `is_deprecated` (bool), `imported_at`.
- Import/validation: `app/catalog/services.py::import_release_
  manifest` (checksum mismatch against an existing row rejected,
  line 156).

## Real, enforced gates

- **License-level platform allowlist**: `License.allowed_platforms`,
  checked at activation (`PLATFORM_NOT_ALLOWED`).
- **License-level release-channel allowlist**:
  `License.allowed_release_channel_id`, checked at activation
  (`RELEASE_CHANNEL_NOT_ALLOWED`, `activation.py:123-127`).
- **Public read endpoint**: `GET /product-version-check`
  (`app/api/routes.py:19-34`, serializer `app/api/serializers.py:
  61-72`) exposes `is_current_stable`/`is_deprecated`/
  `release_channel`/`artifact_checksum` — but this route lives in the
  explicitly prototype-only, disabled-by-default `/api/v1` blueprint
  (docstring: "no live product calls this in Phase 5; no
  authentication/signature scheme is wired yet"). **Not a live
  contract a mobile client may depend on today.**

## Real, confirmed absent: minimum-app-version enforcement

Grepped `owner/app/licensing_service/` and `commercial_runtime/
licensing_contracts/` for `min_app_version`/`minimum_app_version`/
`MIN_SUPPORTED` — **zero matches**. The one payload field that reads
as if it were this concept, `app_version_policy`
(`ALLOWED_PAYLOAD_FIELDS`, `assertion_verifier.py:67`), is in the real
server code actually populated from `license_row.allowed_platforms`
(`owner/app/licensing_service/assertions.py:49`) — i.e. a
comma-separated *platform* list, not a version constraint. This is a
real, mislabeled/repurposed field, not a version gate — recorded as
fact, not silently reinterpreted.

There is also no real "withdrawal" concept for a release — the only
`withdraw` hit anywhere in `owner/app/` is an unrelated expense-
approval feature (`app/expenses/approvals.py:141`).

## Real error codes that DO exist at the version/contract layer

`VERSION_NOT_ALLOWED`, `VERSION_UNSUPPORTED`,
`UNSUPPORTED_CONTRACT_VERSION` (all in `PUBLIC_REASON_CODES`,
`owner/app/licensing_service/reason_codes.py`) — these gate the
**protocol contract version** (`contract_version: "v1"` in every
request schema), not an app semantic version. A mobile client must
distinguish "my request used an unsupported wire-protocol version"
(real, enforced) from "my app build is too old to trust" (not
enforced by Owner today, so a mobile client cannot rely on the server
to reject an outdated app build by version number alone).

## Mobile contract implication

`ReleaseCheckRequest`/`ReleaseCheckResult` (M7.17) model the real
`artifact_checksum`/`is_current_stable`/`is_deprecated`/
`release_channel` fields, but the model's own documentation must state
plainly that no live, authenticated route serves this today — a
future milestone (`OWNER_SERVER` + `RELEASE_PIPELINE` work per
`licensing-gap-ownership-matrix.md`) is required before a mobile
client can safely depend on it. `LicensingError` must expose
`VERSION_NOT_ALLOWED`/`VERSION_UNSUPPORTED`/
`UNSUPPORTED_CONTRACT_VERSION` as real, distinct cases, but no
`APP_VERSION_TOO_OLD`-style case may be invented, since Owner has no
such concept today.
