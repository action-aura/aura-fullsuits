# Changelog

All notable changes to Aura FullSuits are recorded here. Format loosely follows Keep a Changelog; dates are extraction-session dates, not release dates until this ships commercially.

## [Unreleased]

### Added
- Phase 0: repository discovery and migration planning docs (`docs/migration/source-inventory.md`, `dependency-map.md`, `extraction-plan.md`, `risk-register.md`).
- Phase 1: project scaffold (directory tree, root docs, requirements files).
- Phase 2: Aura Retail extracted from Action Aura Enterprise (`products/retail/`).
- Phase 2B: Retail import/export parity (`products/retail/backend/api/import_api.py`, Retail-scoped extraction of the source universal import engine), localization parity (`products/retail/frontend/{i18n.js,locales/}`, `POST /api/auth/language`), a real executed Windows PyInstaller build with a found-and-fixed frozen-path bug, and independence verification. Test total: 116/116 passing.

### Fixed
- `products/retail/backend/app.py`: static asset serving (locale files, `i18n.js`, `subsystem-retail.js`) 404'd in a PyInstaller-frozen build because `Path(__file__)` does not reliably resolve inside a bundled archive. Fixed by detecting `sys.frozen` and using `sys._MEIPASS` as the bundle root, matching the existing pattern in `config.py`.
