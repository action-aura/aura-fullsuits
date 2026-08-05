package com.actionaura.retail.importing

/**
 * M6.19 -- real, deterministic source-content hashing for
 * `ImportDryRun.sourceHash`. Real, disclosed finding: M5.8 never built
 * a real hash function -- every M5.8 test constructed `ImportDryRun`
 * with a literal test string (`"hash-abc"`), and no `commonMain`
 * cryptographic hash dependency exists in this module. Real, deliberate
 * choice for THIS purpose: FNV-1a 64-bit, pure Kotlin, no new
 * dependency -- appropriate because `sourceHash` exists to detect
 * "the file changed since the dry-run was issued"
 * (`import-commit-revalidation.md`), not as a security/tamper-proofing
 * primitive; a real cryptographic hash (SHA-256) would be the real
 * choice if this value were ever used as a security boundary, which it
 * is not (`import-commit-revalidation.md`'s own real, disclosed
 * limitation already covers token-integrity scope).
 */
object ImportSourceHasher {
    private const val FNV_OFFSET_BASIS = -0x340d631b7bdddcdbL // 14695981039346656037 as signed Long
    private const val FNV_PRIME = 0x100000001b3L

    fun hash(bytes: ByteArray): String {
        var hash = FNV_OFFSET_BASIS
        for (b in bytes) {
            hash = hash xor (b.toLong() and 0xFF)
            hash *= FNV_PRIME
        }
        return hash.toULong().toString(16)
    }
}
