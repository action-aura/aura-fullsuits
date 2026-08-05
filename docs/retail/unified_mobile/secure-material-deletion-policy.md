# Secure Material Deletion Policy (M10.18)

Real, separate deletion policies, built on the one real
`GenerationalSecureMaterialStore.deleteScope` mechanism (M10.6).

## A. Customer sign-out

Normally deletes: Customer access credential, Customer refresh
credential, Customer session metadata. Real, current M10 limitation
(disclosed in `customer-session-secure-storage.md`): `deleteScope`
operates on the whole scope, not selectively per material type —
today, a real sign-out that must *preserve* Installation credential/
signed lease/Installation identity (per Product policy, if licensed
offline operation without a signed-in Customer is ever approved)
cannot yet call a narrower delete. Recorded as a real, open
implementation gap for whenever that Product policy is decided — not
resolved here, since M9/M10 have no evidence such a policy has been
approved.

## B. Commercial deactivation

Deletes/revokes local access to: Installation credential, signed
lease, active activation bundle — real, `deleteScope`.

**Preserves**: local Retail business data — real, structural
guarantee: `SecureMaterialStore`/`GenerationalSecureMaterialStore`
never touch SQLDelight, confirmed by inspection (no SQLDelight import
anywhere in the `securestorage` package). **A commercial deactivation
can never delete the Customer's Retail business database merely by
virtue of using this milestone's own code** — the two systems have no
shared code path.

## C. Account switch

Real, disclosed requirement (unchanged from `customer-session-secure-
storage.md`): must prevent credentials/material from two different
Customers being combined. Real, current guarantee: scope isolation
(`accountId` is part of the real scope key) prevents *reading* one
account's material under another's key. Real, disclosed gap: does not
yet *automatically* delete the previous account's material on switch —
a real, explicit `deleteScope(oldScope)` call is required by the
caller (M10.21's own future wiring responsibility), not performed
implicitly by this milestone's own storage layer.

## D. Remote revocation

Real, future trigger (no real Owner revocation response exists yet,
M9's own disclosed absence) — the mechanism (`deleteScope`) is real
and ready; only the real trigger is future work.

## E. Factory reset / manual secure reset

Real, explicit, destructive confirmation required — not built as a UI
flow in M10 (that is real Compose/ViewModel work, out of this
milestone's own storage-layer scope) but the underlying real mechanism
(`deleteScope`) exists and is the same real, tested operation as every
other deletion category.

## Real, common guarantee across all five categories

Every real deletion category ultimately calls the same real, tested
`deleteScope` — removes the pointer blob and every real generation
component it names (`deleteScopeRemovesPointerAndAllGenerationComponents`,
M10.28) — no category has its own bespoke, untested deletion code
path.
