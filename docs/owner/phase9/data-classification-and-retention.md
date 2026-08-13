# Phase 9 Milestone 17 — Data Classification and Retention

## Owner's real data classes (confirmed from the real schema, not assumed)

| Data class | Purpose | Access role | Retention | Deletion/offboarding behavior |
|---|---|---|---|---|
| Staff identities | Login/RBAC | Self + Super Admin | Life of employment + audit-retention overlap | `disable_staff` (real, existing) — soft-disable, not hard-delete (audit trail integrity) |
| Customer organizations | Commercial relationship record | Sales/Support/Super Admin | Life of relationship + legal/audit minimum | Anonymize legal_name on request once no active license/subscription remains, retain the audit trail |
| Subscriptions/plans | Commercial state | Sales/Finance/Super Admin | Same as customer record | Same |
| Payment records | Manually recorded (Phase 8, this phase does not add gateway integration) | Finance/Super Admin | Per real accounting/legal retention requirement (not defined by this codebase — an external policy decision) | Retained per that policy, never deleted solely on customer request if a legal retention duty exists |
| Licenses | Commercial entitlement | Sales/Support/Super Admin | Same as subscription | Same |
| Installations | Device binding | Support/Super Admin | Life of the license + a grace period for support investigation | Deactivated, retained for audit |
| Public device keys / fingerprints | Identity verification only — never a private key | Support/Super Admin (read), system (write) | Same as installation | Same |
| Commercial state | Enforcement | System, read by Support/Super Admin | Live, current-state only (not itself historically retained beyond `owner_audit_log`) | n/a |
| Operational notifications | Internal ops queue | Support/Super Admin | Short (operational, not historical record) | Pruned per existing notification-cleanup behavior |
| Support references | Ticket/incident linkage | Support | Per incident retention below | Retained with the incident record |
| Audit events | Immutable compliance/security record | Super Admin (read), system (write, append-only) | Long — this is the record of record for "what happened", never pruned casually | Never deleted; this is the one class this codebase deliberately makes append-only and hash-chained |
| System logs | Operational | Infra operator | Per `logging-and-redaction-policy.md` (real deployment: rotation/size-bounded, not yet configured against a real host) | Rotated per policy |
| Backups | Disaster recovery | Infra operator only | 14-day rolling (`backup-policy.md`) | Pruned automatically per retention |

## Explicitly excluded (Owner never receives this data at all — structural, not policy)

Patient records, appointments, prescriptions, diagnoses, Clinic notes, Clinic invoice line detail,
Retail sales/receipt lines, Retail stock quantities, suppliers, Retail customer records, product
databases, backup contents, local export contents. Confirmed structurally in Phase 8V-P7
(`android-data-boundary-final.md`), reaffirmed unchanged this phase (`network-and-trust-boundaries.md`).

## Access review, correction, staff-account termination

Real, existing Phase 4/8 RBAC + `disable_staff` mechanics — not rebuilt this phase. Periodic access
review is a real operational habit this document establishes (quarterly review of who has Super
Admin/Finance/Sales roles), not a new technical control.
