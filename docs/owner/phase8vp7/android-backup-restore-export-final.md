# Phase 8V-P7 — Android Backup/Restore/Export — Final

## Result: PASS for backup/restore on both products; export not confirmed as a distinct feature (real,
disclosed finding, not a defect)

## Clinic — real, physical, PASS

1. Baseline: 1 patient ("Extension Success Patient").
2. Real backup created via the product-supported workflow: `Settings -> Backup & restore -> Create
   backup` -> `aura-clinic-backup-20260731T084950Z-app1.0.0-rc.4-schema1.aurabak.zip`, 5 KB, real
   timestamp, filename embeds app version and schema version.
3. Added a synthetic change: created "Post Backup Patient" (`P-1785488157-618`) -- required first
   restoring the subscription to real `ACTIVE` (it was genuinely `RESTRICTED` from Phase 8V-P6's
   emergency-extension test, which had long since expired in real elapsed time; restored via the same
   real Owner renewal pipeline used in Scenario 2). Total patients: 2.
4. Restored the backup via the product-supported workflow (`Restore backup` -> confirm dialog ->
   "Restore complete. Restart the app to continue.").
5. Force-stopped and reopened: **total patients back to 1**, and specifically the *correct* one
   ("Extension Success Patient" present, "Post Backup Patient" gone) -- exact, correct reversion, not
   merely a matching count.

## Retail — real, physical, PASS

Identical real sequence: baseline 1 product (19 in stock), real backup
(`aura-retail-backup-20260731T091011Z-app1.0.0-rc.4-schema1.aurabak.zip`, 7 KB), added "Post Backup
Product" (2 products), restored, force-stopped/reopened, confirmed back to exactly 1 product
("Synthetic Product One", 19 in stock) -- correct.

## Export: not confirmed as a distinct feature

No separate "Export" menu item or action was found in either product's Settings, Backup & restore
screen, or the Billing/Invoice and Products screens explored this session (source XML dumps searched
for "export" text, none found). The `.aurabak.zip` backup file itself is a real, complete, portable
data artifact and may be the product's intended export mechanism, but this was not confirmed as a
separate, distinct "export" affordance the way the governing spec's Part N describes it. Reported
honestly as not located rather than assumed to exist or fabricated as tested.

## Safety checks performed

- No licensing restriction blocked backup/restore for either product (both operations available;
  Clinic's were performed against a genuinely `ACTIVE` state this time, not tested specifically during
  `RESTRICTED` this session, though the product's own UI copy on the Licensing screen explicitly states
  "backup/restore/export remain available" in restricted states, consistent with Phase 8V-P5/P6's own
  incidental confirmation of that UI text).
- No manual database-file copying was used -- both backups went through the real, product-supported
  in-app workflow.
- Logcat for this entire segment: zero forbidden-data matches (two matches were benign Android keyboard
  configuration strings, not real secrets).

## Disposition

Backup/restore: **PASS**, real, physical, both products. Export as a distinct feature: **NOT
VERIFIED** (not located, not fabricated).
