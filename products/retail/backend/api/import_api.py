"""
Aura Retail -- Data Import API (CSV / Excel / JSON / SQLite uploads).

Extracted from Action Aura Enterprise's api/import_api.py (3035 lines, a
universal multi-subsystem import engine covering retail/clinic/hr/inventory/
crm/accounting/pm/marketing). This file keeps only the Retail-relevant slice:

  - SCHEMAS: just the "retail" entities (products, customers, suppliers,
    branches, categories) -- the clinic/hr/crm/accounting/pm/marketing/
    inventory schemas are dropped (out of scope for a standalone product).
  - FIELD_ALIASES / ARABIC_ALIASES: trimmed to the keys Retail's own fields
    actually use (name, sku, barcode, category, cost_price, sell_price,
    tax_rate, unit, reorder_level, initial_stock, phone, email, address,
    loyalty_points, total_spent, status, description) -- dropping ~800 lines
    of accounting/HR/CRM/PM/clinic-only alias vocabulary that Retail's
    entities can never match against.
  - The generic parse/detect/clean/execute pipeline (file parsing, header
    fuzzy-matching incl. Arabic, value sniffing, dedup, validation) is kept
    whole -- it is genuinely shared infrastructure this product needs, not
    platform-wide business logic.
  - Only the 5 retail entity handlers are kept (_handle_retail_*); the
    clinic/hr/crm/accounting/pm/marketing/inventory handlers are dropped.

Cross-system entity auto-detection (e.g. a Retail file being suggested as a
Clinic import) is a natural side effect of the ORIGINAL multi-subsystem
SCHEMAS dict and is NOT reproduced here on purpose -- a standalone Retail
product has no Clinic/HR/CRM entities to detect against. Auto-detection
BETWEEN Retail's own 5 entities (products vs categories vs suppliers vs
customers vs branches) is fully preserved.

No export endpoint exists in the source implementation (confirmed: no
`export` route in api/import_api.py, no export call anywhere in
static/js/subsystem-retail.js). This is not a regression introduced by
extraction -- see docs/migration/retail-parity-matrix.md.
"""
import csv
import io
import re
import json as _json
import uuid as _uuid
from datetime import datetime
from flask import Blueprint, request, jsonify, session

from commercial_runtime.identity.mt_auth import mt_login_required

import_bp = Blueprint('import_api', __name__, url_prefix='/api/import')


def _demo_blocked():
    """Import is a downloaded-system-only feature. Block it in demo mode."""
    if session.get('is_demo_mode'):
        return jsonify({
            'success': False,
            'error': 'Data import is not available in the demo. It is included in your downloaded system.'
        }), 403
    return None


# ── Retail entity schemas ─────────────────────────────────────────────────────
SCHEMAS = {
    'retail': {
        'products': {
            'label': 'Products',
            'description': 'Product catalog with pricing and stock levels',
            'fields': [
                {'key': 'name',          'label': 'Product Name',       'required': True,  'type': 'text',    'example': 'Blue T-Shirt'},
                {'key': 'sku',           'label': 'SKU / Item Code',     'required': True,  'type': 'text',    'example': 'TSH-001'},
                {'key': 'barcode',       'label': 'Barcode',             'required': False, 'type': 'text',    'example': '1234567890123'},
                {'key': 'category',      'label': 'Category',            'required': False, 'type': 'text',    'example': 'Clothing'},
                {'key': 'cost_price',    'label': 'Cost Price',          'required': False, 'type': 'number',  'example': '15.00'},
                {'key': 'sell_price',    'label': 'Selling Price',       'required': True,  'type': 'number',  'example': '29.99'},
                {'key': 'tax_rate',      'label': 'Tax Rate (%)',         'required': False, 'type': 'number',  'example': '15'},
                {'key': 'unit',          'label': 'Unit (pcs/kg/box)',    'required': False, 'type': 'text',    'example': 'pcs'},
                {'key': 'reorder_level', 'label': 'Reorder Level',        'required': False, 'type': 'integer', 'example': '10'},
                {'key': 'initial_stock', 'label': 'Current Stock Qty',    'required': False, 'type': 'number',  'example': '50'},
            ]
        },
        'customers': {
            'label': 'Customers',
            'description': 'Existing customer database',
            'fields': [
                {'key': 'name',           'label': 'Full Name',       'required': True,  'type': 'text',   'example': 'John Smith'},
                {'key': 'phone',          'label': 'Phone Number',    'required': False, 'type': 'text',   'example': '+1 555 000 0001'},
                {'key': 'email',          'label': 'Email Address',   'required': False, 'type': 'email',  'example': 'john@email.com'},
                {'key': 'address',        'label': 'Address',         'required': False, 'type': 'text',   'example': '123 Main St, City'},
                {'key': 'loyalty_points', 'label': 'Loyalty Points',  'required': False, 'type': 'number', 'example': '150'},
                {'key': 'total_spent',    'label': 'Total Spent',     'required': False, 'type': 'number', 'example': '2400.00'},
            ]
        },
        'suppliers': {
            'label': 'Suppliers',
            'description': 'Supplier / vendor list',
            'fields': [
                {'key': 'name',    'label': 'Company Name',   'required': True,  'type': 'text',  'example': 'ABC Wholesale Ltd'},
                {'key': 'phone',   'label': 'Phone Number',   'required': False, 'type': 'text',  'example': '+1 555 000 0002'},
                {'key': 'email',   'label': 'Email Address',  'required': False, 'type': 'email', 'example': 'orders@abc.com'},
                {'key': 'address', 'label': 'Address',        'required': False, 'type': 'text',  'example': '456 Warehouse Blvd'},
            ]
        },
        'branches': {
            'label': 'Branches / Stores',
            'description': 'Store / branch locations',
            'fields': [
                {'key': 'name',    'label': 'Branch Name', 'required': True,  'type': 'text', 'example': 'Downtown Store'},
                {'key': 'address', 'label': 'Address',     'required': False, 'type': 'text', 'example': '12 Market St'},
                {'key': 'phone',   'label': 'Phone',       'required': False, 'type': 'text', 'example': '+1 555 000 0010'},
                {'key': 'status',  'label': 'Status',      'required': False, 'type': 'text', 'example': 'active'},
            ]
        },
        'categories': {
            'label': 'Product Categories',
            'description': 'Product category list',
            'fields': [
                {'key': 'name',        'label': 'Category Name', 'required': True,  'type': 'text', 'example': 'Beverages'},
                {'key': 'description', 'label': 'Description',   'required': False, 'type': 'text', 'example': 'Cold and hot drinks'},
            ]
        },
    },
}

# ── Value parsers ─────────────────────────────────────────────────────────────

def _parse_number(val):
    if val is None or str(val).strip() == '':
        return None
    cleaned = re.sub(r'[^\d.\-]', '', str(val).replace(',', ''))
    try:
        return float(cleaned)
    except ValueError:
        return None

def _parse_integer(val):
    n = _parse_number(val)
    return int(n) if n is not None else None

def _parse_date(val):
    if val is None or str(val).strip() == '':
        return None
    s = str(val).strip()
    formats = [
        '%Y-%m-%d', '%d/%m/%Y', '%m/%d/%Y',
        '%d-%m-%Y', '%m-%d-%Y', '%Y/%m/%d',
        '%d.%m.%Y', '%B %d, %Y', '%b %d, %Y',
        '%d %B %Y', '%d %b %Y',
    ]
    for fmt in formats:
        try:
            return datetime.strptime(s.split('T')[0].split(' ')[0], fmt).strftime('%Y-%m-%d')
        except ValueError:
            continue
    return None

def _parse_text(val):
    if val is None:
        return ''
    return str(val).strip()

def _coerce(value, field_type):
    if field_type == 'number':  return _parse_number(value)
    if field_type == 'integer': return _parse_integer(value)
    if field_type == 'date':    return _parse_date(value)
    return _parse_text(value)

# ── File parsing ──────────────────────────────────────────────────────────────

def _parse_file(file_obj, filename):
    """Parse CSV, Excel (.xlsx/.xls), JSON, or SQLite (.db) upload. Returns (headers, rows-as-dicts)."""
    fname = (filename or '').lower()
    if fname.endswith('.xlsx') or fname.endswith('.xls'):
        try:
            import openpyxl
            wb = openpyxl.load_workbook(file_obj, data_only=True)
            ws = wb.active
            rows = list(ws.iter_rows(values_only=True))
            if not rows:
                return [], []
            headers = [str(c).strip() if c is not None else f'Col{i+1}'
                       for i, c in enumerate(rows[0])]
            data = [
                {headers[i]: (str(r[i]).strip() if r[i] is not None else '')
                 for i in range(min(len(headers), len(r)))}
                for r in rows[1:]
                if any(c is not None and str(c).strip() != '' for c in r)
            ]
            return headers, data
        except Exception as e:
            raise ValueError(f'Could not read Excel file: {e}')
    elif fname.endswith('.json'):
        raw = file_obj.read().decode('utf-8', errors='replace')
        parsed = _json.loads(raw)
        if isinstance(parsed, list) and parsed:
            headers = list(parsed[0].keys())
            return headers, parsed
        raise ValueError('JSON must be an array of objects')
    elif fname.endswith('.db') or fname.endswith('.sqlite') or fname.endswith('.sqlite3'):
        import sqlite3 as _sqlite3, tempfile as _tempfile, os as _os
        tmp = _tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        try:
            tmp.write(file_obj.read())
            tmp.flush(); tmp.close()
            conn = _sqlite3.connect(tmp.name)
            conn.row_factory = _sqlite3.Row
            tables = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()]
            if not tables:
                conn.close()
                raise ValueError('No tables found in this .db file.')
            best, best_count = tables[0], -1
            for t in tables:
                try:
                    cnt = conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
                except Exception:
                    cnt = 0
                if cnt > best_count:
                    best, best_count = t, cnt
            rows = conn.execute(f'SELECT * FROM "{best}" LIMIT 50000').fetchall()
            conn.close()
            if not rows:
                return [], []
            headers = list(rows[0].keys())
            data = [{h: ('' if r[h] is None else str(r[h]).strip()) for h in headers} for r in rows]
            return headers, data
        finally:
            try:
                _os.unlink(tmp.name)
            except Exception:
                pass
    else:
        raw = file_obj.read().decode('utf-8', errors='replace')
        sample = raw[:4096]
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=',;\t|')
        except csv.Error:
            dialect = csv.excel
        reader = csv.DictReader(io.StringIO(raw), dialect=dialect)
        headers = reader.fieldnames or []
        data = [dict(row) for row in reader if any(v and str(v).strip() for v in row.values())]
        return list(headers), data

# ── Auto-suggest column mapping ───────────────────────────────────────────────
# Trimmed to the keys Retail's own schema fields use (see module docstring).
FIELD_ALIASES = {
    'sell_price':        ['price', 'sellprice', 'sellingprice', 'retailprice', 'unitprice',
                          'mrp', 'rate', 'saleprice', 'listprice', 'standardprice', 'msrp',
                          'regularprice', 'normalprice', 'offerprice', 'rrp', 'sp',
                          'netprice', 'nettprice', 'salesprice', 'exvatprice', 'priceexvat',
                          'sellingrate', 'salevalue', 'priceper', 'priceperunit',
                          'unitsellingprice', 'baseprice', 'stdprice', 'currentprice',
                          'actualprice', 'netrate', 'nettrate', 'discountedprice',
                          'finalrate', 'finalprice', 'sellamt', 'saleamt', 'salesamt',
                          'priceamt', 'outprice', 'outrate', 'tradeprice', 'quotedprice',
                          'quotedrate', 'salerate', 'salesrate'],
    'cost_price':        ['cost', 'costprice', 'buyprice', 'purchaseprice', 'wholesaleprice',
                          'unitcost', 'landedcost', 'nettcost', 'invoiceprice', 'cp',
                          'movingavgprice', 'stdcost', 'standardcost', 'avgcost',
                          'buyingprice', 'purchasecost', 'costp', 'inboundcost',
                          'stockcost', 'acquisitioncost', 'purchaserate', 'buyrate',
                          'supplierprice', 'supplierrate', 'vendorcost', 'vendorprice',
                          'vendorrate', 'costofgoods', 'cogsprice'],
    'initial_stock':     ['qty', 'quantity', 'stock', 'onhand', 'instock', 'stockqty',
                          'qtyonhand', 'available', 'currentstock', 'openingstock',
                          'openingqty', 'openingbalance', 'balanceqty', 'balancequantity',
                          'balqty', 'currentqty', 'qtyavailable', 'inventorycount',
                          'inventoryqty', 'stockcount', 'physicalcount', 'physicalqty',
                          'count', 'unrestrictedstock', 'freestockqty', 'availableqty',
                          'stockquantity', 'stockbalance', 'availablestock', 'actualqty',
                          'stocklevel', 'stockinhand', 'closingstock', 'closingqty',
                          'closingbalance', 'qtyinhand', 'totalqty', 'totalstock',
                          'onhandqty', 'currentbalance', 'unitsonhand', 'units',
                          'pcs', 'soh', 'qoh', 'noofunits', 'numberofunits',
                          'stockonhand', 'inventorybalance', 'qtyavail', 'avlqty'],
    'reorder_level':     ['reorder', 'reorderpoint', 'minstock', 'minimumstock', 'minimum',
                          'minqty', 'minimumqty', 'threshold', 'safetystock', 'lowstocklevel',
                          'bufferstock', 'bufferqty', 'rol', 'safetylevel', 'safetyqty',
                          'lowstockamount', 'minlevel', 'minstocklevel', 'reorderqty',
                          'rop', 'minimumreorderqty', 'minorderqty', 'moq',
                          'alertlevel', 'warninglevel', 'triggerlevel', 'replenishmentpoint',
                          'reorderlevel', 'stockthreshold', 'minreorderqty'],
    'sku':               ['sku', 'itemcode', 'productcode', 'code', 'itemno', 'productno',
                          'partnumber', 'partno', 'articleno', 'articlecode', 'stockcode',
                          'stockno', 'prodid', 'productid', 'itemid', 'materialno',
                          'materialcode', 'material', 'partnr', 'artno', 'articlenum',
                          'refcode', 'catalogno', 'modelno', 'modelnumber', 'itemnumber',
                          'productnumber', 'variantsku', 'internalcode', 'internalref',
                          'uniquecode', 'itemref', 'productref', 'stockkeepingunit',
                          'sapcode', 'erpcode', 'systemcode', 'skucode', 'skuid',
                          'inum', 'pnum', 'pcode', 'icode'],
    'barcode':           ['barcode', 'upc', 'ean', 'ean13', 'eancode', 'qrcode', 'isbn',
                          'gtin', 'barcodenum', 'barcodenumber', 'scancode', 'scannedcode',
                          'variantbarcode', 'upccode', 'gtincode', 'upca', 'upce',
                          'productbarcode', 'itembarcode', 'ean8', 'scanbarcode'],
    'tax_rate':          ['taxrate', 'tax', 'vat', 'gst', 'hst', 'vatrate', 'gstrate',
                          'taxpct', 'taxpercent', 'taxpercentage', 'vatpercent', 'taxcode',
                          'gstcode', 'vatcode', 'taxclass', 'vatclass', 'salestax',
                          'salestaxrate', 'taxamt', 'taxamount', 'taxvalue', 'vatamt',
                          'vatamount', 'gstamt', 'gstamount', 'taxbracket', 'taxband',
                          'taxablerate', 'taxperc'],
    'unit':              ['unit', 'uom', 'unitofmeasure', 'unitofmeasurement', 'measureunit',
                          'salesunit', 'baseunit', 'baseuom', 'baseunitofmeasure',
                          'packtype', 'packunit', 'packaging', 'pack', 'each',
                          'orderunit', 'sellingunit', 'stockunit', 'purchaseunit',
                          'unittype', 'measuretype', 'unitdescription', 'unitname',
                          'unitlabel', 'unitabbrev', 'uomcode', 'uomdesc'],
    'name':              ['name', 'fullname', 'productname', 'itemname', 'customername',
                          'suppliername', 'vendorname', 'vendor', 'supplier', 'client',
                          'contact', 'contactname', 'person', 'article', 'articlename',
                          'materialname', 'materialdescription', 'materialdesc',
                          'title', 'displayname',
                          'firstname', 'lastname', 'surname', 'givenname', 'familyname',
                          'merchantname', 'nameof', 'fname', 'lname', 'sname', 'personname'],
    'phone':             ['phone', 'mobile', 'tel', 'telephone', 'phoneno', 'cell',
                          'cellphone', 'mobilenumber', 'phonenumber', 'contactnumber',
                          'telnumber', 'mobilephone', 'workphone', 'officephone',
                          'officetelephone', 'officetel', 'directphone', 'contactno',
                          'mobiletel', 'mainphone', 'businessphone', 'homephone',
                          'phonenum', 'telnum', 'mobilenum', 'cellnum', 'fax', 'faxno',
                          'faxnumber', 'whatsapp', 'directline', 'phonefield',
                          'mobilefield', 'contactphone', 'ph', 'mob'],
    'email':             ['email', 'emailaddress', 'mail', 'emailid', 'mailid',
                          'workemail', 'corporateemail', 'emailaddr', 'emailcontact',
                          'businessemail', 'officeemail', 'contactemail', 'mainemail',
                          'primaryemail', 'personalemail', 'eaddress', 'electronicmail',
                          'emailfield', 'emailaddr1', 'emailaddress1', 'emailaddr2'],
    'address':           ['address', 'addr', 'streetaddress', 'street', 'fulladdress',
                          'mailingaddress', 'homeaddress', 'officeaddress', 'billingaddress',
                          'deliveryaddress', 'shippingaddress', 'postaladdress',
                          'residence', 'place', 'city', 'addressline', 'addressline1',
                          'addressline2', 'suburb', 'region', 'country', 'province',
                          'state', 'postal', 'postcode', 'zipcode', 'zip',
                          'registeredaddress', 'contactaddress', 'addr1', 'addr2'],
    'status':            ['status', 'state', 'condition', 'flag', 'active', 'isactive',
                          'enabled', 'statuscode', 'statecode', 'statusdesc', 'statedesc',
                          'statuslabel', 'currentstatus'],
    'description':       ['description', 'memo', 'narration', 'narr', 'details', 'detail',
                          'particulars', 'particular', 'remarks', 'remark', 'comment',
                          'desc', 'descr', 'descript', 'dscr', 'info', 'information',
                          'text', 'subject', 'label'],
    'category':          ['category', 'cat', 'productgroup', 'itemgroup', 'materialgroup',
                          'materialtype', 'group', 'productclass', 'productfamily',
                          'family', 'class', 'segment', 'subcategory', 'itemtype',
                          'producttype', 'catname', 'categoryname'],
    'total_spent':       ['totalspent', 'lifetimevalue', 'ltv', 'totalpurchases', 'totalsales',
                          'totalrevenue', 'spent', 'totalspend', 'cumulativespend', 'amountspent',
                          'totalpaid', 'lifetimespend', 'totalbilled', 'purchasetotal'],
    'loyalty_points':    ['loyaltypoints', 'points', 'rewardpoints', 'bonuspoints',
                          'membershippoints', 'pts', 'earnedpoints', 'accruedpoints',
                          'pointsbalance', 'rewardbalance', 'loyaltybalance',
                          'memberpoints', 'clubpoints', 'cashbackpoints', 'pointstotal'],
}

# ── Bilingual (Arabic) column-name synonyms -- trimmed to Retail's field keys ──
ARABIC_ALIASES = {
    'name':        ['الاسم', 'اسم', 'الاسم الكامل', 'اسم العميل', 'اسم المنتج',
                    'اسم الشركة', 'الزبون'],
    'phone':       ['الهاتف', 'هاتف', 'الجوال', 'جوال', 'رقم الهاتف', 'رقم الجوال',
                    'الموبايل', 'موبايل', 'تليفون', 'رقم الاتصال'],
    'email':       ['البريد', 'البريد الالكتروني', 'البريد الإلكتروني', 'الايميل',
                    'ايميل', 'إيميل', 'بريد'],
    'address':     ['العنوان', 'عنوان', 'الموقع', 'المدينة', 'الحي'],
    'description': ['الوصف', 'وصف', 'البيان', 'بيان', 'التفاصيل', 'الملاحظات'],
    'category':    ['الفئة', 'فئة', 'التصنيف', 'تصنيف', 'الصنف', 'المجموعة'],
    'sell_price':  ['سعر البيع', 'سعر بيع'],
    'cost_price':  ['سعر التكلفة', 'التكلفة', 'سعر الشراء'],
    'status':      ['الحالة', 'حالة'],
    'sku':         ['رمز الصنف', 'كود الصنف', 'رقم الصنف', 'الرمز'],
    'barcode':     ['الباركود', 'باركود', 'الرمز الشريطي'],
}


def _normalize(s):
    """Lowercase + strip everything except latin alphanumerics and Arabic letters.
    Arabic is preserved so bilingual headers (الاسم، الهاتف…) match their aliases."""
    s = str(s).strip().lower().replace('ـ', '')  # drop Arabic tatweel
    return re.sub(r'[^a-z0-9؀-ۿ]', '', s)


_ALIASES_NORM = {}
for _k, _al in FIELD_ALIASES.items():
    _ALIASES_NORM[_k] = {_normalize(a) for a in _al}
for _k, _al in ARABIC_ALIASES.items():
    _ALIASES_NORM.setdefault(_k, set()).update(_normalize(a) for a in _al)

_EMAIL_RE   = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


def _sniff_column_type(values):
    vals = [str(v).strip() for v in (values or []) if str(v).strip()]
    if not vals:
        return None
    n = len(vals)
    if sum(1 for v in vals if _EMAIL_RE.match(v)) >= max(1, int(n * 0.6)):
        return 'email'
    if sum(1 for v in vals if _parse_date(v)) >= max(1, int(n * 0.6)):
        return 'date'
    if sum(1 for v in vals if _parse_number(v) is not None) >= max(1, int(n * 0.6)):
        return 'number'
    return 'text'


def _suggest_mapping(schema_fields, file_columns, samples=None):
    """Auto-map file columns to schema fields (header fuzzy match + value sniffing,
    English + Arabic). Returns (suggestions, meta)."""
    samples  = samples or {}
    sniffed  = {col: _sniff_column_type(samples.get(col)) for col in file_columns}

    def score_pair(field, col):
        key      = field['key']
        key_norm = _normalize(key)
        lbl_norm = _normalize(field['label'])
        aliases  = _ALIASES_NORM.get(key, set())
        col_norm = _normalize(col)
        score, reason = 0, ''
        if col_norm and (col_norm == key_norm or col_norm == lbl_norm):
            score, reason = 100, 'Exact name match'
        elif col_norm and col_norm in aliases:
            score, reason = 90, 'Recognised column name'
        elif key_norm and (key_norm in col_norm or col_norm in key_norm):
            score, reason = 75, 'Name contains the field'
        elif lbl_norm and (lbl_norm in col_norm or col_norm in lbl_norm):
            score, reason = 65, 'Label overlap'
        elif any(a and (a in col_norm or col_norm in a) for a in aliases):
            score, reason = 55, 'Partial name match'
        else:
            key_words = [w for w in re.split(r'[_\s]', key) if len(w) > 1]
            lbl_words = [w for w in re.split(r'[_\s]', field['label'].lower()) if len(w) > 1]
            words = {_normalize(w) for w in (key_words + lbl_words)}
            words = {w for w in words if len(w) >= 2}
            if any(w and (w in col_norm or col_norm in w) for w in words):
                score, reason = 45, 'Keyword match'

        ftype = field.get('type', 'text')
        st    = sniffed.get(col)
        if st:
            agree = ((ftype == 'email' and st == 'email') or
                     (ftype == 'date' and st == 'date') or
                     (ftype in ('number', 'integer') and st == 'number'))
            if score >= 45:
                if agree:
                    score = min(100, score + 10)
                    reason += ' + values agree'
            else:
                if ftype == 'email' and st == 'email':
                    score, reason = 85, 'Values look like emails'
                elif ftype == 'date' and st == 'date':
                    score, reason = 50, 'Values look like dates'
        return score, reason

    pairs = []
    for field in schema_fields:
        for col in file_columns:
            sc, reason = score_pair(field, col)
            if sc >= 45:
                pairs.append((sc, field['key'], col, reason))
    pairs.sort(key=lambda p: -p[0])

    suggestions = {f['key']: None for f in schema_fields}
    meta = {}
    used_cols = set()
    AUTO_APPLY_MIN = 65
    for sc, fkey, col, reason in pairs:
        if fkey in meta or col in used_cols:
            continue
        conf = 'high' if sc >= 90 else ('medium' if sc >= 65 else 'low')
        applied = sc >= AUTO_APPLY_MIN
        if applied:
            suggestions[fkey] = col
            used_cols.add(col)
        meta[fkey] = {'column': col, 'confidence': conf, 'reason': reason,
                      'score': sc, 'applied': applied}
    return suggestions, meta


def _entity_fit(schema_fields, headers, samples):
    suggestions, _ = _suggest_mapping(schema_fields, headers, samples)
    req_keys = {f['key'] for f in schema_fields if f.get('required')}
    req_mapped = sum(1 for k in req_keys if suggestions.get(k))
    total_mapped = sum(1 for v in suggestions.values() if v)
    req_ratio = (req_mapped / len(req_keys)) if req_keys else 1.0
    cov_ratio = (total_mapped / len(schema_fields)) if schema_fields else 0.0
    return round(req_ratio * 0.7 + cov_ratio * 0.3, 3)

# ── Parse endpoint ────────────────────────────────────────────────────────────

@import_bp.route('/schemas', methods=['GET'])
@mt_login_required
def get_schemas():
    return jsonify({'success': True, 'schemas': SCHEMAS})

@import_bp.route('/parse', methods=['POST'])
@mt_login_required
def parse_file_endpoint():
    """Upload a file, return column names, sample values, row count, and auto-detected mapping."""
    blocked = _demo_blocked()
    if blocked:
        return blocked
    if 'file' not in request.files:
        return jsonify({'success': False, 'error': 'No file uploaded'}), 400
    f    = request.files['file']
    system = request.form.get('system', '')
    entity = request.form.get('entity', '')

    try:
        headers, rows = _parse_file(f.stream, f.filename)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400

    if not headers:
        return jsonify({'success': False, 'error': 'File has no column headers'}), 400
    if len(rows) == 0:
        return jsonify({'success': False, 'error': 'File contains no data rows'}), 400

    samples, sniff_samples = {}, {}
    for col in headers:
        vals = []
        for row in rows:
            v = str(row.get(col, '')).strip()
            if v and v.lower() not in ('none', 'null', 'nan'):
                vals.append(v)
            if len(vals) >= 15:
                break
        samples[col] = vals[:3]
        sniff_samples[col] = vals

    schema = SCHEMAS.get(system, {}).get(entity, {})
    schema_fields = schema.get('fields', [])
    if schema_fields:
        suggested, mapping_meta = _suggest_mapping(schema_fields, headers, sniff_samples)
    else:
        suggested, mapping_meta = {}, {}

    entity_suggestions = []
    chosen_fit = _entity_fit(schema_fields, headers, sniff_samples) if schema_fields else 0.0
    for sysname, ents in SCHEMAS.items():
        for ename, sdef in ents.items():
            if sysname == system and ename == entity:
                continue
            fit = _entity_fit(sdef['fields'], headers, sniff_samples)
            if fit >= 0.6 and fit >= chosen_fit + 0.25:
                entity_suggestions.append({
                    'system': sysname, 'entity': ename,
                    'label': sdef.get('label', ename), 'fit': fit,
                })

    _tokens = _all_entity_tokens()
    _chosen_tok = _entity_token(entity)
    _id_col = suggested.get('name') or suggested.get('title')
    _owner = _identity_owner(_id_col, _tokens)
    if _owner and _owner != _chosen_tok:
        _cand = None
        for sysname, ents in SCHEMAS.items():
            for ename, sdef in ents.items():
                if (sysname, ename) in _HANDLERS and _entity_token(ename) == _owner:
                    if sysname == system:
                        _cand = (sysname, ename, sdef)
                        break
                    elif _cand is None:
                        _cand = (sysname, ename, sdef)
            if _cand and _cand[0] == system:
                break
        if _cand and not any(s['system'] == _cand[0] and s['entity'] == _cand[1] for s in entity_suggestions):
            entity_suggestions.insert(0, {'system': _cand[0], 'entity': _cand[1],
                                          'label': _cand[2].get('label', _cand[1]), 'fit': 0.97})

    entity_suggestions.sort(key=lambda a: -a['fit'])
    entity_suggestions = entity_suggestions[:3]

    return jsonify({
        'success': True,
        'columns': headers,
        'samples': samples,
        'total':   len(rows),
        'suggested': suggested,
        'mapping_meta': mapping_meta,
        'chosen_fit': chosen_fit,
        'entity_suggestions': entity_suggestions,
    })


# ── Universal / smart import (combined files → many entities) ─────────────────

_ENTITY_ORDER = ['categories', 'suppliers', 'branches', 'customers', 'products']

def _order_index(entity):
    return _ENTITY_ORDER.index(entity) if entity in _ENTITY_ORDER else len(_ENTITY_ORDER)


def _entity_token(ename):
    base = ename
    if base.endswith('ies'):
        base = base[:-3] + 'y'
    elif base.endswith('es') and not base.endswith('ses'):
        base = base[:-2]
    elif base.endswith('s'):
        base = base[:-1]
    return _normalize(base)


def _all_entity_tokens():
    toks = set()
    for ents in SCHEMAS.values():
        for ename in ents:
            t = _entity_token(ename)
            if t:
                toks.add(t)
    return toks


def _identity_owner(col, tokens):
    if not col:
        return None
    cn = _normalize(col)
    for suffix in ('name', 'title'):
        if cn.endswith(suffix) and len(cn) > len(suffix):
            prefix = cn[:-len(suffix)]
            if prefix in tokens:
                return prefix
    return None


def _file_samples(headers, rows, keep=15):
    samples, sniff = {}, {}
    for col in headers:
        vals = []
        for row in rows:
            v = str(row.get(col, '')).strip()
            if v and v.lower() not in ('none', 'null', 'nan'):
                vals.append(v)
            if len(vals) >= keep:
                break
        samples[col] = vals[:3]
        sniff[col] = vals
    return samples, sniff


@import_bp.route('/detect', methods=['POST'])
@mt_login_required
def detect_entities():
    """Scan an uploaded file against every Retail entity and return the ones it
    covers -- powers the universal importer that splits a file across the right
    Retail entities (e.g. a combined file with both products and suppliers)."""
    blocked = _demo_blocked()
    if blocked:
        return blocked
    if 'file' not in request.files:
        return jsonify({'success': False, 'error': 'No file uploaded'}), 400
    f = request.files['file']
    only_systems = [s for s in (request.form.get('systems', '') or '').split(',') if s]

    try:
        headers, rows = _parse_file(f.stream, f.filename)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    if not headers:
        return jsonify({'success': False, 'error': 'File has no column headers'}), 400
    if not rows:
        return jsonify({'success': False, 'error': 'File contains no data rows'}), 400

    samples, sniff = _file_samples(headers, rows)

    detected = []
    for sysname, ents in SCHEMAS.items():
        if only_systems and sysname not in only_systems:
            continue
        for ename, sdef in ents.items():
            if (sysname, ename) not in _HANDLERS:
                continue
            fields = sdef['fields']
            suggested, meta = _suggest_mapping(fields, headers, sniff)
            fit = _entity_fit(fields, headers, sniff)

            token = _entity_token(ename)
            name_keys = [fl['key'] for fl in fields if fl['key'] in ('name', 'title')]
            name_specific = False
            if token:
                for nk in name_keys:
                    for col in headers:
                        if token in _normalize(col):
                            suggested[nk] = col
                            meta[nk] = {'column': col, 'confidence': 'high',
                                        'reason': 'Matches the data type name',
                                        'score': 95, 'applied': True}
                            name_specific = True
                            break
                    if name_specific:
                        break

            mapped = {k: v for k, v in suggested.items() if v}
            req_keys = {fl['key'] for fl in fields if fl.get('required')}
            req_mapped = sum(1 for k in req_keys if suggested.get(k))
            STRONG_KEYS = {'sku', 'barcode', 'code', 'email'}
            strong_key = any(k in mapped for k in STRONG_KEYS)
            all_req = (req_mapped == len(req_keys))
            real_match = name_specific or strong_key
            if all_req and real_match and fit >= 0.55:
                detected.append({
                    'system': sysname, 'entity': ename,
                    'label': sdef.get('label', ename),
                    'description': sdef.get('description', ''),
                    'fit': fit, 'mapped_count': len(mapped),
                    'order': _order_index(ename),
                    'suggested': suggested, 'mapping_meta': meta, 'fields': fields,
                })
    detected.sort(key=lambda d: (-d['fit'], d['order']))
    seen_labels, unique = set(), []
    for d in detected:
        if d['label'] in seen_labels:
            continue
        seen_labels.add(d['label'])
        unique.append(d)
    return jsonify({
        'success': True,
        'columns': headers, 'samples': samples, 'total': len(rows),
        'detected': unique,
    })


@import_bp.route('/smart-execute', methods=['POST'])
@mt_login_required
def smart_execute():
    """Import one (possibly combined) file into MULTIPLE Retail entities in one
    pass. Accepts the file + `targets` = [{system, entity, mapping}]. Runs each
    target through the normal clean → handler pipeline, in dependency order
    (categories/suppliers/branches before products/customers that reference
    them)."""
    blocked = _demo_blocked()
    if blocked:
        return blocked
    if 'file' not in request.files:
        return jsonify({'success': False, 'error': 'No file uploaded'}), 400
    f = request.files['file']
    try:
        targets = _json.loads(request.form.get('targets', '[]'))
    except Exception:
        return jsonify({'success': False, 'error': 'Invalid targets JSON'}), 400
    if not targets:
        return jsonify({'success': False, 'error': 'No targets selected'}), 400

    try:
        _, rows = _parse_file(f.stream, f.filename)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400

    targets.sort(key=lambda t: _order_index(t.get('entity', '')))
    results = []
    for t in targets:
        system, entity = t.get('system'), t.get('entity')
        mapping = t.get('mapping') or {}
        schema = SCHEMAS.get(system, {}).get(entity)
        handler = _HANDLERS.get((system, entity))
        label = (schema or {}).get('label', entity)
        if not schema or not handler:
            results.append({'entity': entity, 'label': label, 'imported': 0,
                            'skipped': 0, 'error': 'No importer for this type'})
            continue
        schema_fields = {fl['key']: fl for fl in schema['fields']}
        records, report = _clean_records(rows, mapping, schema_fields)
        if not records:
            results.append({'entity': entity, 'label': label, 'imported': 0,
                            'skipped': report['removed_rows'],
                            'note': 'No clean rows for this type after cleaning.'})
            continue
        try:
            res = handler(records)
        except Exception as e:
            res = {'imported': 0, 'error': f'{e}'}
        res['entity'] = entity
        res['label'] = label
        res['removed'] = report['removed_rows']
        results.append(res)

    total_imported = sum(r.get('imported', 0) + r.get('updated', 0) for r in results)
    return jsonify({'success': True, 'results': results, 'total_imported': total_imported})


# ── Data Cleaning Engine ──────────────────────────────────────────────────────

def _dedup_keys(schema_fields):
    for k in ('sku', 'barcode', 'code', 'email'):
        if k in schema_fields:
            return [k]
    return list(schema_fields.keys())


def _clean_records(rows, mapping, schema_fields):
    """Run the cleaning pipeline. Returns (clean_records, report) with a
    detailed, categorised audit log (duplicate/unreadable/missing/empty)."""
    counts = {
        'trimmed': 0, 'empty_removed': 0, 'duplicates_removed': 0,
        'dates_standardized': 0, 'emails_normalized': 0,
        'numbers_coerced': 0, 'rows_skipped': 0,
    }
    categories = {'duplicate': 0, 'unreadable': 0, 'missing': 0, 'empty': 0}
    issues = []
    clean = []
    seen = {}
    dedup_keys = _dedup_keys(schema_fields)

    def _short(v):
        s = str(v)
        return s if len(s) <= 40 else s[:37] + '…'

    for row_idx, raw_row in enumerate(rows, start=2):
        mapped, nonempty = {}, False
        for fk in schema_fields:
            col = mapping.get(fk)
            v = raw_row.get(col, '') if col else ''
            mapped[fk] = v
            if v is not None and str(v).strip() != '':
                nonempty = True

        if not nonempty:
            counts['empty_removed'] += 1
            categories['empty'] += 1
            issues.append({'row': row_idx, 'category': 'empty', 'action': 'removed',
                           'field': '', 'value': '', 'reason': 'Empty record — no values in any mapped column'})
            continue

        rec, problems = {}, []
        for fk, fdef in schema_fields.items():
            raw = mapped[fk]
            ftype = fdef.get('type', 'text')
            label = fdef.get('label', fk)

            if isinstance(raw, str):
                norm = ' '.join(raw.split())
                if norm != raw:
                    counts['trimmed'] += 1
                raw = norm

            if ftype == 'date':
                d = _parse_date(raw)
                if raw and d is None:
                    problems.append({'category': 'unreadable', 'field': label,
                                     'value': raw, 'reason': f'{label}: unreadable date "{_short(raw)}"'})
                elif d and d != str(raw):
                    counts['dates_standardized'] += 1
                rec[fk] = d
            elif ftype in ('number', 'integer'):
                n = _coerce(raw, ftype)
                if raw not in (None, '') and n is None:
                    problems.append({'category': 'unreadable', 'field': label,
                                     'value': raw, 'reason': f'{label}: not a number "{_short(raw)}"'})
                elif n is not None and str(n) != str(raw):
                    counts['numbers_coerced'] += 1
                rec[fk] = n
            elif ftype == 'email':
                e = raw.strip().lower() if isinstance(raw, str) else raw
                if e and e != raw:
                    counts['emails_normalized'] += 1
                rec[fk] = e
            else:
                rec[fk] = raw

            if fdef.get('required') and (rec[fk] is None or str(rec[fk]).strip() == ''):
                problems.append({'category': 'missing', 'field': label,
                                 'value': '', 'reason': f'Missing required field: {label}'})

        if problems:
            counts['rows_skipped'] += 1
            cat = 'unreadable' if any(p['category'] == 'unreadable' for p in problems) else 'missing'
            categories[cat] += 1
            issues.append({'row': row_idx, 'category': cat, 'action': 'skipped',
                           'field': problems[0]['field'], 'value': problems[0]['value'],
                           'reason': '; '.join(p['reason'] for p in problems)})
            continue

        key_parts = [str(rec.get(k, '')).strip().lower() for k in dedup_keys]
        if any(key_parts):
            key = tuple(key_parts)
            matched_on = ', '.join(dedup_keys)
        else:
            key = tuple(str(rec.get(k, '')).strip().lower() for k in schema_fields)
            matched_on = 'all fields (identical row)'

        if key in seen:
            counts['duplicates_removed'] += 1
            categories['duplicate'] += 1
            issues.append({'row': row_idx, 'category': 'duplicate', 'action': 'removed',
                           'field': matched_on,
                           'value': ' | '.join(p for p in key_parts if p),
                           'reason': f'Duplicate of row {seen[key]} (matched on {matched_on})'})
            continue
        seen[key] = row_idx
        clean.append(rec)

    log = [
        {'step': 'Whitespace trimming & normalization', 'affected': counts['trimmed'],
         'detail': 'Stripped leading/trailing and collapsed repeated spaces in text cells.'},
        {'step': 'Empty-record removal', 'affected': counts['empty_removed'],
         'detail': 'Removed rows that contained no usable values.'},
        {'step': 'Type validation & coercion', 'affected': counts['numbers_coerced'],
         'detail': 'Parsed numeric/integer fields and validated their types.'},
        {'step': 'Date standardization (ISO YYYY-MM-DD)', 'affected': counts['dates_standardized'],
         'detail': 'Converted mixed date formats to a single ISO standard.'},
        {'step': 'Email normalization', 'affected': counts['emails_normalized'],
         'detail': 'Lower-cased and trimmed email addresses.'},
        {'step': 'Duplicate detection & removal', 'affected': counts['duplicates_removed'],
         'detail': f'Removed exact duplicates keyed on: {", ".join(dedup_keys)}.'},
        {'step': 'Error / unreadable-value handling', 'affected': categories['unreadable'],
         'detail': 'Excluded rows whose values could not be parsed to the expected type.'},
        {'step': 'Missing-required handling', 'affected': categories['missing'],
         'detail': 'Excluded rows missing one or more required fields.'},
    ]
    report = {
        'total_rows': len(rows),
        'clean_rows': len(clean),
        'removed_rows': counts['empty_removed'] + counts['duplicates_removed'] + counts['rows_skipped'],
        'categories': categories,
        'log': log,
        'issues': issues[:200],
        'counts': counts,
    }
    return clean, report


@import_bp.route('/clean', methods=['POST'])
@mt_login_required
def clean_preview():
    """Parse + run the cleaning pipeline and return the audit report WITHOUT importing."""
    blocked = _demo_blocked()
    if blocked:
        return blocked
    if 'file' not in request.files:
        return jsonify({'success': False, 'error': 'No file uploaded'}), 400
    f = request.files['file']
    system = request.form.get('system', '')
    entity = request.form.get('entity', '')
    try:
        mapping = _json.loads(request.form.get('mapping', '{}'))
    except Exception:
        return jsonify({'success': False, 'error': 'Invalid mapping JSON'}), 400

    schema = SCHEMAS.get(system, {}).get(entity)
    if not schema:
        return jsonify({'success': False, 'error': f'Unknown entity: {system}/{entity}'}), 400

    try:
        _, rows = _parse_file(f.stream, f.filename)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400

    schema_fields = {fl['key']: fl for fl in schema['fields']}
    clean, report = _clean_records(rows, mapping, schema_fields)
    report['success'] = True
    report['sample'] = clean[:8]
    return jsonify(report)


# ── Execute endpoint ──────────────────────────────────────────────────────────

@import_bp.route('/execute', methods=['POST'])
@mt_login_required
def execute_import():
    """Re-upload file + mapping → clean, validate, and import all clean rows."""
    blocked = _demo_blocked()
    if blocked:
        return blocked
    if 'file' not in request.files:
        return jsonify({'success': False, 'error': 'No file uploaded'}), 400
    f       = request.files['file']
    system  = request.form.get('system', '')
    entity  = request.form.get('entity', '')
    mapping_raw = request.form.get('mapping', '{}')

    try:
        mapping = _json.loads(mapping_raw)
    except Exception:
        return jsonify({'success': False, 'error': 'Invalid mapping JSON'}), 400

    schema = SCHEMAS.get(system, {}).get(entity)
    if not schema:
        return jsonify({'success': False, 'error': f'Unknown entity: {system}/{entity}'}), 400

    try:
        _, rows = _parse_file(f.stream, f.filename)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400

    schema_fields = {f['key']: f for f in schema['fields']}
    records, report = _clean_records(rows, mapping, schema_fields)

    if not records:
        return jsonify({
            'success': False,
            'error': f'No valid rows after cleaning. {report["removed_rows"]} rows removed/skipped.',
            'cleaning': report,
        }), 400

    handler = _HANDLERS.get((system, entity))
    if not handler:
        return jsonify({'success': False, 'error': f'Import handler not found for {system}/{entity}'}), 400

    try:
        result = handler(records)
    except Exception as e:
        return jsonify({'success': False, 'error': f'Import failed: {e}'}), 500

    result['cleaning']   = report
    result['row_errors'] = [{'row': i['row'], 'errors': [i['reason']]} for i in report['issues']][:50]
    result['skipped']    = result.get('skipped', 0) + report['removed_rows']

    landed = result.get('imported', 0) + result.get('updated', 0)
    if landed == 0:
        result['status'] = 'none'
    elif result['skipped'] > 0:
        result['status'] = 'partial'
    else:
        result['status'] = 'ok'

    warnings = []
    if result.get('hint'):
        warnings.append(result['hint'])
    if landed == 0 and result['skipped'] > 0 and not warnings:
        warnings.append(
            f"All {result['skipped']} rows were skipped, so nothing was imported. "
            f"Check the column mapping, and that any linked records exist first.")
    result['warnings'] = warnings
    return jsonify({'success': True, **result})

# ── Entity handlers ───────────────────────────────────────────────────────────

def _cid():
    return session.get('company_id') or session.get('mt_company_id', 1)

def _uid():
    return session.get('mt_user_id') or session.get('user_id', 'system')


def _handle_retail_products(records):
    from database.schema import get_retail_conn
    conn  = get_retail_conn()
    cur   = conn.cursor()
    cid   = _cid()
    imported, skipped, dupes, cat_cache = 0, 0, 0, {}

    branch = conn.execute("SELECT id FROM branches WHERE company_id=? LIMIT 1", (cid,)).fetchone()
    bid = branch['id'] if branch else None
    if not bid:
        cur.execute("INSERT INTO branches (company_id,name) VALUES (?,'Main Store')", (cid,))
        bid = cur.lastrowid

    for rec in records:
        cat_name = (rec.get('category') or '').strip()
        cat_id   = None
        if cat_name:
            if cat_name not in cat_cache:
                row = conn.execute("SELECT id FROM categories WHERE company_id=? AND name=?", (cid, cat_name)).fetchone()
                if not row:
                    new_cat_id = str(_uuid.uuid4())
                    cur.execute("INSERT INTO categories (id,company_id,name) VALUES (?,?,?)", (new_cat_id, cid, cat_name))
                    cat_cache[cat_name] = new_cat_id
                else:
                    cat_cache[cat_name] = row['id']
            cat_id = cat_cache[cat_name]

        sku = (rec.get('sku') or '').strip()
        if not sku:
            skipped += 1; continue

        existing = conn.execute("SELECT id FROM products WHERE company_id=? AND sku=?", (cid, sku)).fetchone()
        if existing:
            cur.execute("""
                UPDATE products SET name=?, barcode=?, category_id=?, cost_price=?,
                    sell_price=?, tax_rate=?, unit=?, reorder_level=?
                WHERE company_id=? AND sku=?
            """, (rec.get('name',''), rec.get('barcode',''), cat_id,
                  rec.get('cost_price') or 0, rec.get('sell_price') or 0,
                  rec.get('tax_rate') or 0, rec.get('unit','pcs') or 'pcs',
                  rec.get('reorder_level') or 5,
                  cid, sku))
            pid = existing['id']
            dupes += 1
        else:
            pid = str(_uuid.uuid4())
            cur.execute("""
                INSERT INTO products (id,company_id,sku,barcode,name,category_id,cost_price,
                                      sell_price,tax_rate,unit,reorder_level,status)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,'active')
            """, (pid, cid, sku, rec.get('barcode',''), rec.get('name',''), cat_id,
                  rec.get('cost_price') or 0, rec.get('sell_price') or 0,
                  rec.get('tax_rate') or 0, rec.get('unit','pcs') or 'pcs',
                  rec.get('reorder_level') or 5))
            imported += 1

        init_stock = rec.get('initial_stock')
        if init_stock and float(init_stock) > 0 and bid:
            cur.execute("""
                INSERT OR IGNORE INTO inventory_balances (company_id,product_id,branch_id,quantity_on_hand)
                VALUES (?,?,?,0)
            """, (cid, pid, bid))
            cur.execute("""
                UPDATE inventory_balances SET quantity_on_hand=?
                WHERE company_id=? AND product_id=? AND branch_id=?
            """, (float(init_stock), cid, pid, bid))

    conn.commit(); conn.close()
    return {'imported': imported, 'updated': dupes, 'skipped': skipped,
            'message': f'{imported} new products, {dupes} updated.'}


def _handle_retail_customers(records):
    from database.schema import get_retail_conn
    conn = get_retail_conn()
    cur  = conn.cursor()
    cid  = _cid()
    imported, updated = 0, 0
    for rec in records:
        name = (rec.get('name') or '').strip()
        if not name: continue
        email = (rec.get('email') or '').strip().lower()
        existing = conn.execute(
            "SELECT id FROM customers WHERE company_id=? AND (email=? AND email!='')",
            (cid, email)).fetchone() if email else None
        lp = rec.get('loyalty_points')
        lp = float(lp) if lp is not None else 0
        ts = rec.get('total_spent')
        ts = float(ts) if ts is not None else 0
        if existing:
            cur.execute("UPDATE customers SET name=?,phone=?,address=?,loyalty_points=?,total_spent=? WHERE id=?",
                        (name, rec.get('phone',''), rec.get('address',''), lp, ts, existing['id']))
            updated += 1
        else:
            nid = str(_uuid.uuid4())
            cur.execute("INSERT INTO customers (id,company_id,name,phone,email,address,loyalty_points,total_spent) VALUES (?,?,?,?,?,?,?,?)",
                        (nid, cid, name, rec.get('phone',''), email, rec.get('address',''), lp, ts))
            imported += 1
    conn.commit(); conn.close()
    return {'imported': imported, 'updated': updated, 'message': f'{imported} new customers, {updated} updated.'}


def _handle_retail_suppliers(records):
    from database.schema import get_retail_conn
    conn = get_retail_conn()
    cur  = conn.cursor()
    cid  = _cid()
    imported = 0
    for rec in records:
        name = (rec.get('name') or '').strip()
        if not name: continue
        existing = conn.execute("SELECT id FROM suppliers WHERE company_id=? AND name=?", (cid, name)).fetchone()
        if existing: continue
        cur.execute("INSERT INTO suppliers (company_id,name,phone,email,address) VALUES (?,?,?,?,?)",
                    (cid, name, rec.get('phone',''), rec.get('email',''), rec.get('address','')))
        imported += 1
    conn.commit(); conn.close()
    return {'imported': imported, 'message': f'{imported} suppliers imported.'}


def _handle_retail_branches(records):
    from database.schema import get_retail_conn
    conn = get_retail_conn(); cur = conn.cursor(); cid = _cid()
    imported = 0
    for rec in records:
        name = (rec.get('name') or '').strip()
        if not name: continue
        if conn.execute("SELECT id FROM branches WHERE company_id=? AND name=?", (cid, name)).fetchone():
            continue
        status = (rec.get('status') or 'active').strip().lower()
        cur.execute("INSERT INTO branches (company_id,name,address,phone,status) VALUES (?,?,?,?,?)",
                    (cid, name, rec.get('address',''), rec.get('phone',''), status))
        imported += 1
    conn.commit(); conn.close()
    return {'imported': imported, 'message': f'{imported} branches imported.'}


def _handle_retail_categories(records):
    from database.schema import get_retail_conn
    conn = get_retail_conn(); cur = conn.cursor(); cid = _cid()
    imported = 0
    for rec in records:
        name = (rec.get('name') or '').strip()
        if not name: continue
        if conn.execute("SELECT id FROM categories WHERE company_id=? AND name=?", (cid, name)).fetchone():
            continue
        cur.execute("INSERT INTO categories (id,company_id,name,description) VALUES (?,?,?,?)",
                    (str(_uuid.uuid4()), cid, name, rec.get('description','')))
        imported += 1
    conn.commit(); conn.close()
    return {'imported': imported, 'message': f'{imported} categories imported.'}


_HANDLERS = {
    ('retail', 'products'):   _handle_retail_products,
    ('retail', 'customers'):  _handle_retail_customers,
    ('retail', 'suppliers'):  _handle_retail_suppliers,
    ('retail', 'branches'):   _handle_retail_branches,
    ('retail', 'categories'): _handle_retail_categories,
}
