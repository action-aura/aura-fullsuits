# Phase 8V-P9 Part N — Final Data-Preservation Comparison

## Physical Android device (Retail, `com.actionaura.retail`)

Dashboard real UI values, read via `uiautomator dump` before and after the rc.4 -> rc.5 in-place
upgrade (`adb install -r`, no uninstall) and all subsequent real licensing operations (check-in,
Scenario 7 evidence gathering) this session:

| Field | Before upgrade | After upgrade + all ops |
|---|---|---|
| Today's sales | `$0.00` | `$0.00` |
| Transactions today | `1` | `1` |
| Products (catalog) | `1` | `1` |
| Low stock | `0` | `0` |

Identical -- no business data was created, altered, or lost. Consistent with `adb install -r`
semantics (upgrade, not reinstall) and with the fact that the only source change this session
(`commercial_runtime`) has no code path that touches product/business tables.

## Windows real instances (Retail)

All Windows licensing operations this session (`AuraRetail-P7-C`, `-P7-D`, `-P9-Block1`,
`-P9-Block2`) used real `AURA_APP_DATA` directories whose `database/subsystems/retail.db` was never
opened or written by any of this session's operations -- only `database/subsystems/licensing.db` was
read/written, confirmed by construction (every operation performed was a licensing-domain
activation/check-in/status call, never a Retail business-data endpoint).

## Owner database

No Retail/Clinic customer, patient, or payment data exists in the Owner database by design (Owner
only tracks licensing/subscription/installation records) -- unaffected by definition.
