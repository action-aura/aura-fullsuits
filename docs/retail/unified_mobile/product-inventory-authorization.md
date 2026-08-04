# Aura Retail Unified Mobile — Product/Inventory Authorization (M5.5.13)

## Real, honest scope finding — checked before writing anything

Grepped the entire `mobile/aura-retail-unified/shared/src` tree for any existing permission/RBAC concept: the only match is `platform.PermissionManager` (`PlatformContracts.kt`), an OS-level permission contract (`PlatformPermission.CAMERA`) for Milestone 14's barcode-scanner work — it has nothing to do with business authorization (who may create a product, adjust stock, etc.). **No Kotlin-side RBAC/authorization system exists anywhere in this codebase today.**

The governing spec's own instruction for this section is explicit: "Do not create a second RBAC authority. Reuse the current Retail authentication and permission model." The real, cited current model (product-inventory-authority-audit.md #7) is `commercial_runtime/identity/mt_auth.py`'s `@mt_login_required`/`@mt_require_subsystem`, plus `commercial_runtime/licensing_contracts/flask_guard.py`'s `@require_license_capability` — both are **Python, server-side, HTTP-request-scoped decorators**. There is no equivalent authentication/session/permission concept anywhere in this Kotlin Multiplatform codebase, because none of Milestones 7-10 (Owner licensing authority audit, multi-device activation, secure credential storage, offline lease enforcement) have been reached yet — `LicensingRepository`/`UserRepository`/`SessionRepository` are still empty M5.1 boundary stubs (`RepositoryBoundaries.kt`), exactly as documented then.

## What this means for M5.5.13, honestly

**"Reuse the current... model" is not achievable yet** — there is nothing on the Kotlin side to reuse, and building a *new* one now would be exactly the "second RBAC authority" the spec explicitly forbids. Fabricating a placeholder permission-check use case now, ahead of the real activation/session model Milestones 7-10 will define, would either (a) be meaningless (checking against nothing real) or (b) risk becoming the accidental "second authority" if a later milestone doesn't fully replace it.

**Disposition**: M5.5.13 is explicitly deferred, not silently skipped and not falsely claimed done. Every M5.5 use case (`CreateProductUseCase`, `AdjustInventoryUseCase`, etc.) is a plain, unauthenticated function today — same as every M5.1-M5.4 use case before it. The real integration point is already identified: once Milestone 9/10 delivers a real session/permission model, it plugs in as one more constructor dependency to each use case (the same pattern every other cross-cutting dependency in this codebase already uses — `UnicodeTextNormalizer`, `CategoryRepository`, etc.), requiring no redesign of the use-case layer itself.

## What was NOT deferred

Two structural properties already function as a defense-in-depth business-isolation boundary, independent of any user-level RBAC, and are real and tested today:

- **Every repository method and use case is `companyId`-scoped** — cross-business data leakage is structurally rejected (`RepositoryError.NotFound`) regardless of who is asking, proven repeatedly across M5.1-M5.5 (`categoryIsScopedByCompanyId`, `createRejectsCrossBusinessCategoryAssignment`, etc.).
- **License-capability gating** (`@require_license_capability`) remains a real, server-side control on the Python backend for as long as this mobile client talks to that backend (pre-Milestone-11 offline-lease work) — this milestone does not remove or bypass it.
