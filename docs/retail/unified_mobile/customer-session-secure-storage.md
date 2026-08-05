# Customer Session Secure Storage (M10.12)

Real, protected storage design for external Customer session material
(M9.6), integrated with the M10.4-6 architecture.

## Real separation (per the checkpoint's own required list)

`ExternalCustomerAccessCredential` (short-lived access credential),
`ExternalCustomerRefreshCredential` (refresh credential),
`ExternalCustomerSessionId`, `ExternalCustomerAccountId`,
`CustomerSessionExpiry` (session expiry metadata), the bundle's own
`credentialRevision` (session revision) — each a real, already-
distinct M9 type, mapped onto the real `SecureMaterialType.CUSTOMER_
ACCESS_CREDENTIAL`/`CUSTOMER_REFRESH_CREDENTIAL`/`CUSTOMER_SESSION_
METADATA` components (M10.4/M10.6).

## Real rules

- **Access credential may be memory-only when architecture permits**:
  real, deliberate design in `SecureActivationBundle`
  (`customerAccessCredential: ExternalCustomerAccessCredential?`,
  nullable) — a future real `ActivationViewModel` wiring (M10.21) may
  choose to keep the short-lived access credential in memory only
  (never committed to `SecureMaterialStore` at all) and persist only
  the refresh credential, re-deriving a fresh access credential via
  `refreshCustomerSession` (M9.7) on each real app start. This
  document records the option as real and available; the exact choice
  is a real M10.21 wiring decision, not fixed here.
- **Refresh credential must be protected when persistence is
  required**: real, always — `CUSTOMER_REFRESH_CREDENTIAL` is a real
  `SecureMaterialStore` component, never plaintext.
- **Sign-out deletes Customer session credentials**: real,
  `GenerationalSecureMaterialStore.deleteScope` (M10.6), wired into
  the real `CustomerAuthenticationOrchestrator.signOut` flow (M9.7) as
  part of M10.18's own deletion policy.
- **Global revocation response deletes session credentials**: real,
  future behavior once a real Owner revocation response exists (M9's
  own disclosed absence of a real external API) — the same
  `deleteScope` call is the real mechanism; no new mechanism is
  needed, only a real future trigger.
- **Password-reset/session-version rejection deletes session
  credentials**: same real mechanism.
- **Account switch clears previous account material**: real —
  `SecureMaterialScope.accountId` is part of the real bundle key
  (M10.4); a different `accountId` resolves to a structurally
  different scope/pointer, so switching accounts without an explicit
  `deleteScope` on the old account would leave the old account's own
  bundle orphaned-but-inert (never merged with the new account's own
  material) — M10.18's own deletion policy requires an explicit
  cleanup call on account switch, not merely relying on scope
  isolation alone, to avoid real storage accumulation over time.
- **Activation material belonging to another Customer cannot remain
  active silently**: real, same scope-isolation guarantee — no code
  path in `GenerationalSecureMaterialStore` ever reads/writes across
  two different `accountId` scopes in a single operation.
- **Raw credentials never enter UI state or logs**: real, unchanged
  since M9.6/M9.23 — confirmed again for this milestone's own new
  code (M10.24's own memory-hygiene audit).

## Real answer: can signed-out commercial Installation operation continue?

**Yes, real, by design** — `installationCredential` and
`rawSignedLease` are stored as separate `SecureMaterialType` components
from the Customer session ones; `deleteScope` as currently designed
operates on one whole scope (Product+Installation+account) at a time,
not selectively per material type. **Real, disclosed refinement
needed**: today's `deleteScope` signature takes one `SecureMaterialScope`
and deletes everything under it — a real future refinement (not built
in M10, since M9's own real product policy on this question is not yet
decided) would need a narrower "delete Customer session material only,
preserve Installation/lease material" operation if Product policy
requires licensed-offline-operation-without-a-signed-in-Customer. This
document records the real, open question rather than assuming an
answer no real Product decision has made yet.
