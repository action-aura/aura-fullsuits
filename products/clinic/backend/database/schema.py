"""
Aura Clinic -- database schema and connection.

Owns clinic.db: clinic_patients, clinic_appointments, clinic_doctors,
clinic_visits, clinic_visit_notes, clinic_services, clinic_invoices,
clinic_invoice_items, clinic_payments, clinic_prescriptions,
clinic_audit_log, clinic_followups, clinic_lab_expenses.

Extracted verbatim (schema + seed) from Action Aura Enterprise's
database/subsystem_db.py `init_clinic`/`_seed_clinic` (lines 5109-5390),
which also owned schema for every other subsystem in that monolith -- see
docs/migration/clinic-dependency-map.md. Only the clinic-relevant slice plus
the generic connection helpers it needs are kept here.

`_seed_clinic` is preserved for parity/dev use but is only ever invoked when
`not _is_standalone()` (see `init_clinic` below) -- a packaged customer
build, and every test in this suite, always sets AURA_STANDALONE=1, so no
demo patient/doctor/appointment records are ever created outside of a raw
local dev checkout. This is the same mechanism Retail already relies on
(products/retail/backend/database/schema.py) -- see Phase 3 definition of
done item 5 ("clean database initialization... zero patient records").
"""
import os
import sqlite3
from datetime import datetime, timedelta

_app_data = os.environ.get('AURA_APP_DATA')
if _app_data:
    BASE_DIR = os.path.join(_app_data, 'database')
else:
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
SUBSYS_DIR = os.path.join(BASE_DIR, 'subsystems')


def _get_path(name):
    os.makedirs(SUBSYS_DIR, exist_ok=True)
    return os.path.join(SUBSYS_DIR, f'{name}.db')


def _conn(name):
    c = sqlite3.connect(_get_path(name), timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA busy_timeout=30000")
    c.execute("PRAGMA foreign_keys=ON")
    return c


def _is_standalone():
    """Return True when running as a packaged customer build (no seed data)."""
    try:
        import config as _cfg
        return getattr(_cfg, 'IS_STANDALONE', False)
    except ImportError:
        return False


def get_clinic_conn():
    return _conn('clinic')


def sub_create(conn_fn, table, data):
    conn = conn_fn()
    try:
        keys = ', '.join(data.keys())
        placeholders = ', '.join(['?'] * len(data))
        conn.execute(f"INSERT INTO {table} ({keys}) VALUES ({placeholders})", list(data.values()))
        conn.commit()
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    finally:
        conn.close()


def init_clinic():
    conn = get_clinic_conn()
    cur = conn.cursor()
    cur.executescript("""
    CREATE TABLE IF NOT EXISTS clinic_patients (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER DEFAULT 1,
        patient_code TEXT UNIQUE NOT NULL,
        name TEXT NOT NULL,
        dob TEXT,
        gender TEXT,
        phone TEXT,
        email TEXT,
        address TEXT,
        blood_type TEXT,
        emergency_contact TEXT,
        emergency_phone TEXT,
        notes TEXT,
        status TEXT DEFAULT 'active',
        created_by TEXT,
        updated_by TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS clinic_appointments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER DEFAULT 1,
        patient_id INTEGER NOT NULL,
        doctor_id INTEGER,
        appointment_dt TEXT NOT NULL,
        reason TEXT,
        status TEXT DEFAULT 'scheduled',
        checked_in_at TEXT,
        notes TEXT,
        created_by TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (patient_id) REFERENCES clinic_patients(id)
    );
    CREATE TABLE IF NOT EXISTS clinic_doctors (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER DEFAULT 1,
        name TEXT NOT NULL,
        specialty TEXT,
        phone TEXT,
        email TEXT,
        status TEXT DEFAULT 'active',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS clinic_visits (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER DEFAULT 1,
        patient_id INTEGER NOT NULL,
        doctor_id INTEGER,
        appointment_id INTEGER,
        status TEXT DEFAULT 'active',
        diagnosis TEXT,
        treatment TEXT,
        follow_up_date TEXT,
        completed_at TEXT,
        created_by TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (patient_id) REFERENCES clinic_patients(id)
    );
    CREATE TABLE IF NOT EXISTS clinic_visit_notes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        visit_id INTEGER NOT NULL,
        company_id INTEGER DEFAULT 1,
        note_type TEXT DEFAULT 'general',
        content TEXT NOT NULL,
        created_by TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (visit_id) REFERENCES clinic_visits(id)
    );
    CREATE TABLE IF NOT EXISTS clinic_services (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER DEFAULT 1,
        name TEXT NOT NULL,
        description TEXT,
        price REAL DEFAULT 0,
        category TEXT,
        status TEXT DEFAULT 'active',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS clinic_invoices (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER DEFAULT 1,
        invoice_number TEXT UNIQUE NOT NULL,
        patient_id INTEGER,
        visit_id INTEGER,
        subtotal REAL DEFAULT 0,
        tax REAL DEFAULT 0,
        total REAL DEFAULT 0,
        amount_paid REAL DEFAULT 0,
        status TEXT DEFAULT 'unpaid',
        created_by TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (patient_id) REFERENCES clinic_patients(id)
    );
    CREATE TABLE IF NOT EXISTS clinic_invoice_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        invoice_id INTEGER NOT NULL,
        service_id INTEGER,
        description TEXT,
        qty REAL DEFAULT 1,
        unit_price REAL DEFAULT 0,
        line_total REAL DEFAULT 0,
        FOREIGN KEY (invoice_id) REFERENCES clinic_invoices(id)
    );
    CREATE TABLE IF NOT EXISTS clinic_payments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER DEFAULT 1,
        invoice_id INTEGER,
        amount_paid REAL DEFAULT 0,
        method TEXT DEFAULT 'cash',
        reference TEXT,
        created_by TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (invoice_id) REFERENCES clinic_invoices(id)
    );
    CREATE TABLE IF NOT EXISTS clinic_prescriptions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER DEFAULT 1,
        patient_id INTEGER,
        visit_id INTEGER,
        items_json TEXT,
        notes TEXT,
        created_by TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (patient_id) REFERENCES clinic_patients(id)
    );
    CREATE TABLE IF NOT EXISTS clinic_audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER DEFAULT 1,
        user_id TEXT,
        action TEXT NOT NULL,
        entity TEXT,
        entity_id INTEGER,
        details TEXT,
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS clinic_followups (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER DEFAULT 1,
        patient_id INTEGER NOT NULL,
        visit_id INTEGER,
        followup_date TEXT,
        weight TEXT,
        blood_pressure TEXT,
        temperature TEXT,
        progress TEXT,
        notes TEXT,
        created_by TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (patient_id) REFERENCES clinic_patients(id)
    );
    CREATE TABLE IF NOT EXISTS clinic_lab_expenses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER DEFAULT 1,
        patient_id INTEGER,
        lab_name TEXT,
        test_name TEXT,
        amount REAL DEFAULT 0,
        expense_date TEXT,
        status TEXT DEFAULT 'recorded',
        notes TEXT,
        created_by TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (patient_id) REFERENCES clinic_patients(id)
    );
    """)
    for _alter in (
        "ALTER TABLE clinic_invoices ADD COLUMN discount REAL DEFAULT 0",
        "ALTER TABLE clinic_invoices ADD COLUMN notes TEXT",
        "ALTER TABLE clinic_invoices ADD COLUMN prescription_id INTEGER",
        "ALTER TABLE clinic_invoices ADD COLUMN accounting_synced INTEGER DEFAULT 0",
        "ALTER TABLE clinic_prescriptions ADD COLUMN invoice_id INTEGER",
    ):
        try:
            cur.execute(_alter)
        except Exception:
            pass  # column already exists
    conn.commit()
    if cur.execute("SELECT COUNT(*) FROM clinic_patients").fetchone()[0] == 0 and not _is_standalone():
        _seed_clinic(conn, cur)
    conn.close()


def _seed_clinic(conn, cur, company_id=1):
    """Seed demo data for ONE company. `company_id` defaults to 1 to preserve
    the existing dev-mode first-boot behaviour. Every INSERT is parameterized
    on company_id -- the source version hardcoded the literal 1 in every
    statement, which meant calling it for any other tenant (e.g. from the
    demo-seed API route, company-scoped like Retail's) would silently write
    that tenant's demo data into company 1's tables instead. Fixed here the
    same way Retail's equivalent _seed_retail bug was fixed in Phase 1 --
    see docs/migration/clinic-extraction-report.md."""
    now = datetime.now()
    cid = company_id
    doctors = [
        (cid, 'Dr. Sarah Al-Mansouri', 'General Practice', '+966-50-111-0001', 'sarah@clinic.com'),
        (cid, 'Dr. James Khalil', 'Pediatrics', '+966-50-111-0002', 'james@clinic.com'),
        (cid, 'Dr. Aisha Noor', 'Internal Medicine', '+966-50-111-0003', 'aisha@clinic.com'),
    ]
    for d in doctors:
        cur.execute("INSERT INTO clinic_doctors (company_id,name,specialty,phone,email) VALUES (?,?,?,?,?)", d)
    doc_ids = [r[0] for r in cur.execute(
        "SELECT id FROM clinic_doctors WHERE company_id=? ORDER BY id", (cid,)
    ).fetchall()]

    services = [
        (cid, 'General Consultation', 'Standard doctor visit', 150, 'Consultation'),
        (cid, 'Blood Test - CBC', 'Complete blood count', 80, 'Lab'),
        (cid, 'X-Ray', 'Standard X-Ray imaging', 200, 'Radiology'),
        (cid, 'ECG', 'Electrocardiogram', 120, 'Cardiology'),
        (cid, 'Dressing & Wound Care', 'Wound management', 60, 'Procedure'),
        (cid, 'Pediatric Consultation', 'Child specialist visit', 180, 'Consultation'),
    ]
    for s in services:
        cur.execute("INSERT INTO clinic_services (company_id,name,description,price,category) VALUES (?,?,?,?,?)", s)

    patients = [
        ('P-1001', 'Mohammed Al-Rashid', '1985-03-12', 'Male', '+966-55-001-0001', 'O+', 'Fatima Al-Rashid', '+966-55-001-0002'),
        ('P-1002', 'Sara Hassan', '1992-07-24', 'Female', '+966-55-001-0003', 'A+', 'Omar Hassan', '+966-55-001-0004'),
        ('P-1003', 'Ahmed Khalid', '1978-11-05', 'Male', '+966-55-001-0005', 'B+', 'Noura Khalid', '+966-55-001-0006'),
        ('P-1004', 'Layla Nasser', '2010-02-18', 'Female', '+966-55-001-0007', 'AB+', 'Nasser Ibrahim', '+966-55-001-0008'),
        ('P-1005', 'Ibrahim Saleh', '1965-09-30', 'Male', '+966-55-001-0009', 'O-', 'Mariam Saleh', '+966-55-001-0010'),
    ]
    pid_map = []
    for p in patients:
        # patient_code must be globally UNIQUE -- prefix with company_id so
        # seeding a second company never collides with the first's codes.
        code = f'{cid}-{p[0]}'
        cur.execute("""INSERT INTO clinic_patients
            (company_id,patient_code,name,dob,gender,phone,blood_type,emergency_contact,emergency_phone,created_by)
            VALUES (?,?,?,?,?,?,?,?,?,'system')""", (cid, code) + p[1:])
        pid_map.append(cur.lastrowid)

    d1, d2, d3 = doc_ids[0], doc_ids[1], doc_ids[2]
    appt_data = [
        (cid, pid_map[0], d1, now.strftime('%Y-%m-%d') + ' 09:00', 'Follow-up', 'completed'),
        (cid, pid_map[1], d2, now.strftime('%Y-%m-%d') + ' 10:30', 'Pediatric check', 'waiting'),
        (cid, pid_map[2], d1, now.strftime('%Y-%m-%d') + ' 11:00', 'Blood pressure review', 'scheduled'),
        (cid, pid_map[3], d2, now.strftime('%Y-%m-%d') + ' 12:00', 'Vaccination', 'scheduled'),
        (cid, pid_map[4], d3, now.strftime('%Y-%m-%d') + ' 14:00', 'Diabetes management', 'in_progress'),
        (cid, pid_map[0], d1, (now - timedelta(days=7)).strftime('%Y-%m-%d') + ' 09:00', 'Initial consult', 'completed'),
        (cid, pid_map[2], d3, (now - timedelta(days=5)).strftime('%Y-%m-%d') + ' 11:00', 'Lab results review', 'completed'),
    ]
    aid_map = []
    for a in appt_data:
        cur.execute("INSERT INTO clinic_appointments (company_id,patient_id,doctor_id,appointment_dt,reason,status,created_by) VALUES (?,?,?,?,?,?,'system')", a)
        aid_map.append(cur.lastrowid)

    visit_data = [
        (cid, pid_map[0], d1, aid_map[0], 'completed', 'Hypertension - controlled', 'Continue medication'),
        (cid, pid_map[4], d3, aid_map[4], 'active', 'Type 2 Diabetes', 'Adjust insulin dosage'),
        (cid, pid_map[0], d1, aid_map[5], 'completed', 'General wellness', 'Healthy'),
        (cid, pid_map[2], d3, aid_map[6], 'completed', 'Elevated cholesterol', 'Diet & statins'),
    ]
    vid_map = []
    for v in visit_data:
        cur.execute("INSERT INTO clinic_visits (company_id,patient_id,doctor_id,appointment_id,status,diagnosis,treatment,created_by) VALUES (?,?,?,?,?,?,?,'system')", v)
        vid_map.append(cur.lastrowid)

    notes = [
        (vid_map[0], cid, 'clinical', 'BP: 130/85. Patient reports improved sleep. Continue losartan 50mg.'),
        (vid_map[1], cid, 'clinical', 'Blood sugar fasting: 180 mg/dL. HbA1c: 8.2%. Adjust metformin dose.'),
        (vid_map[3], cid, 'clinical', 'LDL: 160 mg/dL. Recommend Mediterranean diet. Start atorvastatin 20mg.'),
    ]
    for n in notes:
        cur.execute("INSERT INTO clinic_visit_notes (visit_id,company_id,note_type,content,created_by) VALUES (?,?,?,?,'system')", n)

    inv_data = [
        (cid, pid_map[0], vid_map[0], f'INV-C-{cid}-1001', 150, 0, 150, 150, 'paid'),
        (cid, pid_map[2], vid_map[3], f'INV-C-{cid}-1002', 230, 0, 230, 230, 'paid'),
        (cid, pid_map[4], vid_map[1], f'INV-C-{cid}-1003', 150, 0, 150, 0, 'unpaid'),
    ]
    for inv in inv_data:
        status = inv[8]
        cur.execute("""INSERT INTO clinic_invoices
            (company_id,patient_id,visit_id,invoice_number,subtotal,tax,total,amount_paid,status,created_by)
            VALUES (?,?,?,?,?,?,?,?,?,'system')""", (*inv[:8], status))

    conn.commit()
