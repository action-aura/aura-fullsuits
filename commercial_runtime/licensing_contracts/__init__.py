"""Phase 7 -- shared product-side licensing domain.

Imported by both Retail and Clinic Windows backends directly, and by both
Android apps' embedded Chaquopy backend (commercial_runtime is already staged
into the APK). One implementation, every product consumes it identically --
see docs/licensing/phase7/product-integration-architecture.md for the full
design and the Kotlin-side mapping (Kotlin cannot share this module; it is
reproduced there against the same conformance fixtures in
commercial_runtime/licensing_contracts/tests/fixtures/).
"""
