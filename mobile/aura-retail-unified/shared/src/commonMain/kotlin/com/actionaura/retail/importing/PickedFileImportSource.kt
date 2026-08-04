package com.actionaura.retail.importing

import com.actionaura.retail.platform.PickedFile

/**
 * M5.8.20 -- the real, platform-agnostic bridge between the already-
 * established `platform.FilePicker` contract (M2/M5.1's own
 * `PickedFile(name, bytes)`, unrelated to Import Center at the time it
 * was written) and Import Center's own `ImportSource` contract. Pure
 * `commonMain` -- `PickedFile` carries no platform-specific type, so
 * this class needs no `expect`/`actual` split and is fully testable
 * from `commonTest` on every target, not just Android.
 *
 * Real, disclosed limitation: `FilePicker.pickFile` itself returns the
 * ENTIRE file already read into memory (`bytes: ByteArray`) -- that
 * real platform contract predates Import Center's own resource-limit
 * requirements (`import-resource-limits.md`) and does not support an
 * incremental/bounded read. `readBounded` here still enforces the real
 * size limit, but only AFTER the platform layer already buffered the
 * whole file -- the "reject before allocating the oversized buffer"
 * discipline `ImportSource`'s own KDoc describes is fully achieved by
 * the XLSX/SQLite `androidMain` decoders (which stream), not by this
 * general entry path. A future milestone that needs true streaming
 * bounded reads from the platform picker itself would need to evolve
 * `FilePicker`'s own signature, out of scope here.
 */
class PickedFileImportSource(private val pickedFile: PickedFile, override val descriptor: ImportFileDescriptor) : ImportSource {
    override suspend fun readBounded(limits: ImportLimits): ImportResult<ByteArray> {
        val size = pickedFile.bytes.size.toLong()
        if (size > limits.maxCompressedFileSizeBytes) {
            return ImportResult.Failure(ImportError.LimitExceeded(ImportLimitExceeded("maxCompressedFileSizeBytes", limits.maxCompressedFileSizeBytes, size)))
        }
        return ImportResult.Success(pickedFile.bytes)
    }
}
