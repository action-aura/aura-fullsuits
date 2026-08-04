package com.actionaura.retail.importing.entity

import com.actionaura.retail.importing.ImportEntityType
import com.actionaura.retail.importing.ImportFieldDefinition
import com.actionaura.retail.importing.ImportFieldParser

/**
 * M5.8.9 -- real field definitions for the 5 confirmed real entities
 * (`import-handler-matrix.md`'s own audit of the legacy `SCHEMAS` dict:
 * products/customers/suppliers/branches/categories). Field keys/labels/
 * required-ness are ported exactly; parsers are upgraded to the real
 * M3/M5 domain types (`Money`/`Quantity`/`PercentageRate`) per M5.8.10's
 * own "never implement import-only financial parsing" instruction --
 * the legacy authority's own `_parse_number`/`_coerce` (plain
 * float-based) is NOT ported, since it is exactly the kind of unsafe
 * financial parsing this milestone must not repeat.
 */
object ImportEntitySchemas {

    val PRODUCTS = listOf(
        ImportFieldDefinition("name", "Product Name", required = true, parser = ImportFieldParser.TEXT),
        ImportFieldDefinition("sku", "SKU / Item Code", required = true, parser = ImportFieldParser.TEXT),
        ImportFieldDefinition("barcode", "Barcode", required = false, parser = ImportFieldParser.TEXT),
        ImportFieldDefinition("category", "Category", required = false, parser = ImportFieldParser.TEXT),
        ImportFieldDefinition("cost_price", "Cost Price", required = false, parser = ImportFieldParser.MONEY),
        ImportFieldDefinition("sell_price", "Selling Price", required = true, parser = ImportFieldParser.MONEY),
        ImportFieldDefinition("tax_rate", "Tax Rate (%)", required = false, parser = ImportFieldParser.PERCENTAGE_RATE),
        ImportFieldDefinition("unit", "Unit (pcs/kg/box)", required = false, parser = ImportFieldParser.TEXT),
        ImportFieldDefinition("reorder_level", "Reorder Level", required = false, parser = ImportFieldParser.INTEGER),
        ImportFieldDefinition("initial_stock", "Current Stock Qty", required = false, parser = ImportFieldParser.QUANTITY),
    )

    val CUSTOMERS = listOf(
        ImportFieldDefinition("name", "Full Name", required = true, parser = ImportFieldParser.TEXT),
        ImportFieldDefinition("phone", "Phone Number", required = false, parser = ImportFieldParser.TEXT),
        ImportFieldDefinition("email", "Email Address", required = false, parser = ImportFieldParser.EMAIL),
        ImportFieldDefinition("address", "Address", required = false, parser = ImportFieldParser.TEXT),
        ImportFieldDefinition("loyalty_points", "Loyalty Points", required = false, parser = ImportFieldParser.QUANTITY),
        ImportFieldDefinition("total_spent", "Total Spent", required = false, parser = ImportFieldParser.MONEY),
    )

    val SUPPLIERS = listOf(
        ImportFieldDefinition("name", "Company Name", required = true, parser = ImportFieldParser.TEXT),
        ImportFieldDefinition("phone", "Phone Number", required = false, parser = ImportFieldParser.TEXT),
        ImportFieldDefinition("email", "Email Address", required = false, parser = ImportFieldParser.EMAIL),
        ImportFieldDefinition("address", "Address", required = false, parser = ImportFieldParser.TEXT),
    )

    val BRANCHES = listOf(
        ImportFieldDefinition("name", "Branch Name", required = true, parser = ImportFieldParser.TEXT),
        ImportFieldDefinition("address", "Address", required = false, parser = ImportFieldParser.TEXT),
        ImportFieldDefinition("phone", "Phone", required = false, parser = ImportFieldParser.TEXT),
        ImportFieldDefinition("status", "Status", required = false, parser = ImportFieldParser.STATUS),
    )

    val CATEGORIES = listOf(
        ImportFieldDefinition("name", "Category Name", required = true, parser = ImportFieldParser.TEXT),
        ImportFieldDefinition("description", "Description", required = false, parser = ImportFieldParser.TEXT),
    )

    fun fieldsFor(entityType: ImportEntityType): List<ImportFieldDefinition> = when (entityType) {
        ImportEntityType.PRODUCTS -> PRODUCTS
        ImportEntityType.CUSTOMERS -> CUSTOMERS
        ImportEntityType.SUPPLIERS -> SUPPLIERS
        ImportEntityType.BRANCHES -> BRANCHES
        ImportEntityType.CATEGORIES -> CATEGORIES
    }

    /**
     * Real, curated CORE alias subset -- `import-authority-audit.md`'s
     * own real, cited legacy `FIELD_ALIASES` lists 30-60 English
     * synonyms per field key; porting the full list verbatim is
     * explicitly out of scope for this milestone ("use the existing
     * pipeline as behavioral evidence... not as the new architecture" --
     * the full enumeration is implementation detail, not architecture).
     * This is the real, most-common subset, a deliberate, disclosed
     * scope decision -- not a claim of full legacy parity. Arabic
     * aliases ARE ported in full (the legacy list for Retail's fields is
     * short and was fully captured during the audit).
     */
    val ENGLISH_ALIASES: Map<String, Set<String>> = mapOf(
        "sell_price" to setOf("price", "sellprice", "sellingprice", "retailprice", "unitprice", "mrp", "rate", "saleprice"),
        "cost_price" to setOf("cost", "costprice", "buyprice", "purchaseprice", "wholesaleprice", "unitcost"),
        "initial_stock" to setOf("qty", "quantity", "stock", "onhand", "instock", "stockqty", "openingstock", "currentstock"),
        "reorder_level" to setOf("reorder", "reorderpoint", "minstock", "minimumstock", "minqty", "threshold", "safetystock"),
        "sku" to setOf("sku", "itemcode", "productcode", "code", "itemno", "productno", "partnumber"),
        "barcode" to setOf("barcode", "upc", "ean", "ean13", "isbn", "gtin", "scancode"),
        "tax_rate" to setOf("taxrate", "tax", "vat", "gst", "vatrate", "gstrate"),
        "unit" to setOf("unit", "uom", "unitofmeasure", "packtype", "packunit"),
        "name" to setOf("name", "fullname", "productname", "itemname", "customername", "suppliername", "companyname", "branchname", "categoryname"),
        "phone" to setOf("phone", "mobile", "tel", "telephone", "cell", "contactnumber"),
        "email" to setOf("email", "emailaddress", "mail"),
        "address" to setOf("address", "addr", "streetaddress", "fulladdress"),
        "status" to setOf("status", "state", "active", "isactive"),
        "description" to setOf("description", "memo", "narration", "details", "remarks", "desc"),
        "category" to setOf("category", "cat", "productgroup", "itemgroup", "group"),
        "total_spent" to setOf("totalspent", "lifetimevalue", "ltv", "totalpurchases"),
        "loyalty_points" to setOf("loyaltypoints", "points", "rewardpoints"),
    )

    /** Real, fully-ported bilingual (Arabic) aliases for Retail's fields (`import-authority-audit.md`'s own citation of the legacy `ARABIC_ALIASES` dict). */
    val ARABIC_ALIASES: Map<String, Set<String>> = mapOf(
        "name" to setOf("الاسم", "اسم", "الاسم الكامل", "اسم العميل", "اسم المنتج", "اسم الشركة", "الزبون"),
        "phone" to setOf("الهاتف", "هاتف", "الجوال", "جوال", "رقم الهاتف", "رقم الجوال", "الموبايل", "موبايل"),
        "email" to setOf("البريد", "البريد الالكتروني", "البريد الإلكتروني", "الايميل", "ايميل", "إيميل"),
        "address" to setOf("العنوان", "عنوان", "الموقع", "المدينة", "الحي"),
        "description" to setOf("الوصف", "وصف", "البيان", "بيان", "التفاصيل", "الملاحظات"),
        "category" to setOf("الفئة", "فئة", "التصنيف", "تصنيف", "الصنف", "المجموعة"),
        "sell_price" to setOf("سعر البيع", "سعر بيع"),
        "cost_price" to setOf("سعر التكلفة", "التكلفة", "سعر الشراء"),
        "status" to setOf("الحالة", "حالة"),
        "sku" to setOf("رمز الصنف", "كود الصنف", "رقم الصنف", "الرمز"),
        "barcode" to setOf("الباركود", "باركود", "الرمز الشريطي"),
    )
}
