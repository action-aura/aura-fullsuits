# Phase 8V-P — Final Certificate Continuity (Part E)

## Android: NOT VERIFIED this session (no build performed — see `physical-device-readiness.md`)

Signing certs remain, unchanged, on disk from every prior phase (never touched this session):
Clinic `35508048cee7776ca94a45a9f04c6a870edf98729429482a8ad44e89dd0bbbf2`, Retail
`cae6b18450c14a71eba47545e5b3ba52089eef0397d5881f4c1dc7d5a4b7d32d` (per
`docs/owner/phase8/phase8-scope-and-baseline.md`'s own record). No signing key was regenerated,
touched, or even read this session — Android build tooling was never invoked.

## Windows: no code-signing certificate exists in this environment (unchanged, disclosed since
Wave 1B)

Both rc.3 installers are unsigned, same as every rc.1/rc.2 artifact before them. "Continuity" in the
code-signing sense doesn't apply where nothing was ever signed to begin with — this is a known,
disclosed, unchanged limitation, not a regression introduced this session.
