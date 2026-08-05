package com.actionaura.retail.importing

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNotEquals

class ImportSourceHasherTest {

    @Test
    fun theSameRealBytesAlwaysProduceTheSameRealHash() {
        val bytes = "name,description\nBeverages,Drinks\n".encodeToByteArray()
        assertEquals(ImportSourceHasher.hash(bytes), ImportSourceHasher.hash(bytes.copyOf()))
    }

    @Test
    fun differentRealContentProducesADifferentRealHash() {
        val a = "name,description\nBeverages,Drinks\n".encodeToByteArray()
        val b = "name,description\nSnacks,Chips\n".encodeToByteArray()
        assertNotEquals(ImportSourceHasher.hash(a), ImportSourceHasher.hash(b))
    }

    @Test
    fun emptyBytesProduceARealDeterministicHashNeverACrash() {
        assertEquals(ImportSourceHasher.hash(ByteArray(0)), ImportSourceHasher.hash(ByteArray(0)))
    }
}
