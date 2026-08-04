package com.actionaura.retail.financial

import kotlin.test.Test
import kotlin.test.assertTrue

/**
 * M3.5 -- string-parsing boundary differential cases (Quantity.parse /
 * Money.parse vs Python's real `float(x)`, the parser create_sale()/
 * create_return() actually call). Real Python evidence gathered this
 * milestone (see intentional-financial-differences.md for the full,
 * exact float() behavior table):
 *
 *   float("nan") -> nan (no raise)      float("Infinity") -> inf (no raise)
 *   float("  5.5  ") -> 5.5             float("+5") -> 5.0
 *   float(".5") -> 0.5                  float("5.") -> 5.0
 *   float("5_000") -> 5000.0            float("٥5") -> 55.0 (!)
 *   float("5,000") -> raises            float("0x10") -> raises
 *   float("") -> raises                 float("abc") -> raises
 *
 * Quantity.parse/Money.parse deliberately reject NaN/Infinity (closing
 * the real defect, invariant #7a) and deliberately do NOT replicate
 * Python's more exotic float() permissiveness (underscore-grouped
 * digits, Arabic-Indic digit tolerance, bare leading/trailing dot,
 * leading '+') -- those were never a deliberate product requirement,
 * just accidental generality of Python's general-purpose float()
 * converter, and a stricter POS input parser is the right canonical
 * choice, not a defect to port. See intentional-financial-differences.md.
 */
class ParsingDifferentialTest {

    @Test
    fun matchesPython_wellFormedDecimal() {
        assertTrue(Quantity.parse("5") is FinancialResult.Success)
        assertTrue(Quantity.parse("5.5") is FinancialResult.Success)
        assertTrue(Money.parse("5.50") is FinancialResult.Success)
    }

    @Test
    fun matchesPython_whitespaceTrimmed() {
        // float("  5.5  ") == 5.5 -- real Python behavior, matched here.
        val r = Quantity.parse("  5.5  ")
        assertTrue(r is FinancialResult.Success)
        assertTrue((r as FinancialResult.Success).value == Quantity.parse("5.5").getOrNull())
    }

    @Test
    fun matchesPython_emptyStringRejected() {
        assertTrue(Quantity.parse("") is FinancialResult.Failure)
        assertTrue(Money.parse("") is FinancialResult.Failure)
    }

    @Test
    fun matchesPython_commaSeparatorRejected() {
        // float("5,000") raises ValueError -- real Python behavior, matched.
        assertTrue(Quantity.parse("5,000") is FinancialResult.Failure)
    }

    @Test
    fun matchesPython_nonNumericGarbageRejected() {
        assertTrue(Quantity.parse("abc") is FinancialResult.Failure)
        assertTrue(Quantity.parse("0x10") is FinancialResult.Failure)
    }

    @Test
    fun intentionallyDivergesFromPython_nanAndInfinityRejected() {
        // Real Python defect closed (invariant #7a): float("nan")/float("Infinity")
        // do NOT raise in the real backend. Quantity/Money explicitly reject them.
        for (bad in listOf("nan", "NaN", "NAN", "Infinity", "inf", "-inf", "-Infinity")) {
            assertTrue(Quantity.parse(bad) is FinancialResult.Failure, "expected rejection for $bad")
            assertTrue(Money.parse(bad) is FinancialResult.Failure, "expected rejection for $bad")
        }
    }

    @Test
    fun matchesPython_exponentialNotationAccepted() {
        // float("1e3") == 1000.0 -- a real, deliberately-kept Python behavior
        // (exponential notation is standard decimal-literal syntax, not an
        // exotic permissiveness quirk like underscore grouping).
        val r = Quantity.parse("1e3")
        assertTrue(r is FinancialResult.Success)
        assertTrue((r as FinancialResult.Success).value == Quantity.parse("1000").getOrNull())
    }
}
