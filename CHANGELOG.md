# Changelog

All notable changes to Aura FullSuits are recorded here. Format loosely follows Keep a Changelog; dates are extraction-session dates, not release dates until this ships commercially.

## [Unreleased]

### Added
- Phase 0: repository discovery and migration planning docs (`docs/migration/source-inventory.md`, `dependency-map.md`, `extraction-plan.md`, `risk-register.md`).
- Phase 1: project scaffold (directory tree, root docs, requirements files).
- Phase 2: Aura Retail extracted from Action Aura Enterprise (`products/retail/`).
- Phase 2B: Retail import/export parity (`products/retail/backend/api/import_api.py`, Retail-scoped extraction of the source universal import engine), localization parity (`products/retail/frontend/{i18n.js,locales/}`, `POST /api/auth/language`), a real executed Windows PyInstaller build with a found-and-fixed frozen-path bug, and independence verification. Test total: 116/116 passing.
- `scripts/sync/aura-sync.{ps1,sh}`: two-way git auto-sync for teammates sharing this repo across separate local clones — interval-based fetch/auto-commit/pull/push with pre-merge conflict prediction (`git merge-tree`), secret/size/path guards, auto-pause on conflict or repeated failure, and a structured audit log. See `docs/ops/auto-sync.md`. Added `.gitattributes` (line-ending normalization) as a prerequisite.
- Phase 1 (`docs/einvoicing/phase1/`): Jordan JoFotara e-invoicing for Retail and Clinic — opt-in, default off, no effect on any install that doesn't enable it. Shared pipeline in `commercial_runtime/einvoicing/` (settings, kill switch, encrypted credential storage, gapless per-company sequence numbers, generic UBL 2.1 document builder, QR rendering, an async submission outbox with a provider-agnostic interface and a `MockProvider` default, a background worker, and an HTTP/admin surface), thin product-side wiring, and a self-contained settings page for both products. Real ISTD connectivity is deliberately unimplemented (`providers/direct_istd.py` raises `NotImplementedError`) pending official integration docs and sandbox credentials — see `docs/einvoicing/phase2/`. Android UI not built this wave (`docs/einvoicing/phase1/phase1-residual-risk-register.md`). 97% test coverage on the shared module (169 tests); verified with a real PyInstaller build and a live kill-switch drill against a running install.

### Fixed
- `products/retail/backend/app.py`: static asset serving (locale files, `i18n.js`, `subsystem-retail.js`) 404'd in a PyInstaller-frozen build because `Path(__file__)` does not reliably resolve inside a bundled archive. Fixed by detecting `sys.frozen` and using `sys._MEIPASS` as the bundle root, matching the existing pattern in `config.py`.
