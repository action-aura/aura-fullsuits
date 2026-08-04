package com.actionaura.retail.importing.json

import com.actionaura.retail.importing.ImportError
import com.actionaura.retail.importing.ImportLimitExceeded
import com.actionaura.retail.importing.ImportLimits
import com.actionaura.retail.importing.ImportResult

/**
 * M5.8.6 -- a real, pre-parse, string-aware scan of raw JSON text for
 * two things `kotlinx.serialization.json` cannot detect once parsed:
 * excessive nesting depth (rejected before allocating a deep tree) and
 * duplicate object keys (once parsed into a `JsonObject`, a duplicate
 * key has already silently resolved to its last value -- the ambiguity
 * is only observable here, at the raw-text level).
 *
 * Tracks an explicit frame stack (object-with-its-own-key-set, or
 * array) so key tracking is scoped correctly to each real nesting
 * level -- a key colliding in two DIFFERENT objects is not a
 * duplicate; only a repeated key within the SAME object is.
 */
object JsonStructuralScanner {

    private sealed class Frame {
        class Obj(val keys: MutableSet<String> = mutableSetOf()) : Frame()
        object Arr : Frame()
    }

    fun scan(text: String, limits: ImportLimits): ImportResult<Unit> {
        val stack = ArrayDeque<Frame>()
        var depth = 0
        var inString = false
        var escapeNext = false
        var awaitingKey = false
        var collectingKey = false
        val keyBuffer = StringBuilder()
        var i = 0
        val n = text.length

        while (i < n) {
            val c = text[i]
            if (inString) {
                when {
                    escapeNext -> escapeNext = false
                    c == '\\' -> escapeNext = true
                    c == '"' -> {
                        inString = false
                        if (collectingKey) {
                            val key = keyBuffer.toString()
                            val top = stack.lastOrNull()
                            if (top is Frame.Obj && !top.keys.add(key)) {
                                return ImportResult.Failure(ImportError.MalformedContent("duplicate JSON object key \"$key\" -- ambiguous, never silently resolved to the last value"))
                            }
                            collectingKey = false
                            keyBuffer.clear()
                        }
                    }
                    collectingKey -> keyBuffer.append(c)
                }
                i++
                continue
            }
            when (c) {
                '"' -> {
                    inString = true
                    collectingKey = awaitingKey && stack.lastOrNull() is Frame.Obj
                    if (collectingKey) keyBuffer.clear()
                }
                '{' -> {
                    depth++
                    if (depth > limits.maxJsonDepth) {
                        return ImportResult.Failure(ImportError.LimitExceeded(ImportLimitExceeded("maxJsonDepth", limits.maxJsonDepth.toLong(), depth.toLong())))
                    }
                    stack.addLast(Frame.Obj())
                    awaitingKey = true
                }
                '[' -> {
                    depth++
                    if (depth > limits.maxJsonDepth) {
                        return ImportResult.Failure(ImportError.LimitExceeded(ImportLimitExceeded("maxJsonDepth", limits.maxJsonDepth.toLong(), depth.toLong())))
                    }
                    stack.addLast(Frame.Arr)
                    awaitingKey = false
                }
                '}', ']' -> {
                    depth--
                    if (stack.isNotEmpty()) stack.removeLast()
                    awaitingKey = false
                }
                ':' -> awaitingKey = false
                ',' -> awaitingKey = stack.lastOrNull() is Frame.Obj
                else -> { /* whitespace/literal characters -- no structural significance here */ }
            }
            i++
        }
        return ImportResult.Success(Unit)
    }
}
