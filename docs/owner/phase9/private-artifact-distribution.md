# Phase 9 Milestone 14 — Private Artifact Distribution

## Status: NOT VERIFIED — no real artifacts to distribute yet (Milestone 13), no real host to serve them from (Milestone 12)

## Chosen approach for the real deployment (design decision, not built this session)

Signed, expiring download URLs served from the same staging host, behind the same Caddy reverse proxy
— not a separate public marketplace, not a third-party file host. Rationale: reuses the same TLS/
firewall/access-logging boundary already designed for staging (`staging-architecture.md`), avoids
introducing a new external service/trust boundary for a single-digit pilot user count.

## Requirements for the real implementation

- A new Owner route (e.g. `/releases/<token>`) issuing a time-limited signed URL per approved pilot
  user — reuses the existing `owner/app/releases` blueprint already present in this codebase (found
  during this session's `create_app()` review, registered as `releases_bp`) rather than a new
  subsystem; extending it is a real, scoped follow-up, not built this session (no artifact exists to
  serve).
- SHA-256 checksum displayed alongside every download link.
- Version and release notes displayed.
- Android sideload instructions (Settings -> allow install from this source, tied to the specific
  approved distribution point, not "allow from anywhere").
- Windows SmartScreen expectation documented (`pilot-installation-guide.md`) — the existing rc.5/rc.6
  signing certificate is a real code-signing certificate, not EV, so a first-run SmartScreen prompt is
  expected and must be explained to the pilot user in advance, not silently worked around.
- No directory listing — the download route serves exactly one artifact per valid token, nothing else.
- Every download logged (structured, per Milestone 8's logging) and access individually revocable
  (invalidate the token).
- Old versions retained per the rollback policy (`deployment-packaging.md`'s image/tag rollback,
  extended to product artifacts).
- No signing key or secret ever placed in the distribution directory — only the already-signed,
  already-public artifact files themselves.

## What this session confirmed instead

The existing `owner/app/releases` blueprint exists and is already registered in `create_app()` — a real
starting point for this feature, not a green field, discovered while reviewing the app factory this
phase (not previously documented as relevant to distribution in any file this session read).
