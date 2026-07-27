# Phase 8V — Residual Risk Register

| # | Item | Severity | Status |
|---|---|---|---|
| 1 | Physical Android/Windows validation of all 7 Part AB scenarios | Blocks final tag | Not done -- no device access this environment. See `phase8v-physical-validation-report.md`. |
| 2 | `installations.transition` (generic route) allows DEACTIVATED/REPLACED with an optional, often-blank reason, alongside the new mandatory-reason `release_device_slot()`/`replace_device_slot()` routes | Low (restricting-only, no privilege gain) | Documented, deliberately not fixed this phase (domain-redesign out of scope). Recommend closing in the same pass that eventually tightens shared/loosely-gated tables generally. |
| 3 | No reconciliation "repair" workflow (generic approve/apply-repair) exists -- every finding is resolved via the specific existing domain action it points at | None (by design) | Governing brief's Part K assumed a capability Milestone 6 never built. Inventing one now would be new domain design. Not a gap in what Milestone 6 shipped. |
| 4 | No Owner-wide Arabic/RTL localization | Low (consistent with 4 phases of prior design) | Not built. Owner has been English-only by design since Phase 5. |
| 5 | Retail Android's licensing screen has zero Arabic string coverage (pre-existing, not Phase 8V's gap) | Low | `tr()`'s English fallback degrades gracefully. Backfilling is a distinct, larger localization task. |
| 6 | Broader product renewal/expiry UX (renewal-in-progress, pilot, emergency-extension, past-due/grace banners reading the new Part W assertion fields) not built on either product | Medium (feature completeness, not security) | Only the PENDING-activation message (the one item actually blocking Milestone 5's rollout) was built this phase. See `product-renewal-ux-evidence.md`. |
| 7 | No Kotlin/Python conformance fixture files exist anywhere in the repo, despite docs describing the intent | Low-medium (this exact gap is what let the `ALLOWED_PAYLOAD_FIELDS` regression through undetected for a full milestone) | Noted in Phase 8 Milestone 7's own docs as a known gap; this session's live-wire harness is a partial, ad-hoc substitute for one scenario, not a real fixture-file system. Recommend building real shared fixtures generated FROM a real Owner response (like this session's captured evidence) rather than hand-written, so this class of drift can't recur silently. |
| 8 | No formal WCAG accessibility audit (screen reader, contrast ratio, focus-order) run on the new UI | Low | Basic accessibility hygiene followed (labels, confirm dialogs, text-plus-color status, real table headers) but not formally audited. |
| 9 | No real logcat/Windows product-log capture (only structural log-content verification) | Low-medium, same root cause as #1 | See `phase8v-log-privacy-report.md`. |

Nothing above is a P0 or P1 as of this report -- item 7 is the one genuine security-adjacent process
gap (it's what let a real defect ship silently for a milestone), recorded as a process-improvement
recommendation rather than a live defect (the live defect it enabled is already fixed, item 
notwithstanding).
