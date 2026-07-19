"""
Aura Clinic -- Clinic & Patient Management API.

Extracted verbatim (business logic unchanged) from Action Aura Enterprise's
api/subsystems/clinic_api.py -- see docs/migration/clinic-extraction-report.md
for the full list of documented corrective fixes applied in Phase 3 (each
with a test): stack-trace-to-client removed from create_patient's error
response; prescriptions.items_json now written as real JSON; the internal
event-bus URL is configurable (off by default) instead of hardcoded;
demo-wipe/demo-seed hardened (mode gate + admin-only + confirmation token +
full company scoping, closing a cross-tenant data-destruction gap);
create_patient/create_appointment validate their required fields upfront
(closing a connection-leak-on-crash bug); and a cross-tenant IDOR
(insecure direct object reference) across 8 routes was fixed via the
_owned() helper below (see its docstring).
"""
import os
import time, requests
from decimal import Decimal, ROUND_HALF_UP
from flask import Blueprint, request, jsonify, session
from commercial_runtime.identity.mt_auth import mt_login_required, mt_require_subsystem, require_clinic_role
from database.schema import get_clinic_conn, sub_create, init_clinic
from datetime import datetime, timedelta
import random

clinic_bp = Blueprint('clinic_api', __name__, url_prefix='/api/sub/clinic')

# ── Auto-init: ensure clinic tables exist on first import ─────────────────────
# Redundant with app.py's explicit init_app() -> init_clinic() call, kept for
# fidelity with the source's own defensive pattern (idempotent CREATE TABLE
# IF NOT EXISTS, so calling it twice is harmless).
try:
    init_clinic()
except Exception as _e:
    print(f"[clinic_api] init_clinic warning: {_e}")

def _cid(): return session.get('company_id') or session.get('mt_company_id', 1)
def _uid(): return session.get('mt_user_id') or session.get('user_id', 'system')

def _audit(conn, action, entity, entity_id, details=''):
    try:
        conn.execute(
            'INSERT INTO clinic_audit_log (company_id,user_id,action,entity,entity_id,details) VALUES (?,?,?,?,?,?)',
            (_cid(), _uid(), action, entity, entity_id, details)
        )
    except Exception: pass

def _emit(event, payload):
    """Best-effort local event emission. See products/retail/backend/api/retail_api.py's
    identical _emit() docstring -- same treatment, same reasoning."""
    bus_url = os.environ.get('AURA_EVENT_BUS_URL')
    if not bus_url:
        return
    try:
        requests.post(bus_url, json={
            'event_type': event, 'source_system': 'Clinic',
            'company_id': _cid(), 'payload': payload
        }, timeout=1)
    except Exception: pass

def _owned(conn, table, row_id, cid):
    """Phase 3 security fix: verifies a foreign-key id (patient_id, visit_id,
    invoice_id, ...) supplied by the caller actually belongs to the caller's
    own company before it is used to create or link a new record.

    The source implementation inserted these foreign ids directly with no
    ownership check at all -- e.g. add_note(vid) tagged the new note with
    the CALLER's company_id but never verified `vid` (the visit) belonged
    to that company, so any authenticated Company A user could attach a
    clinical note, follow-up, prescription, invoice, or payment to a
    Company B patient/visit/invoice simply by guessing or incrementing an
    integer id. record_payment was the worst case: it read AND wrote a
    `clinic_invoices` row with no `company_id` filter at all on either
    statement, so any authenticated user of ANY company could mark ANY
    other company's invoice paid. Found by an automated security review of
    this extraction's commits, not by the original test suite -- see
    docs/migration/clinic-extraction-report.md and
    docs/security/clinic-rbac-matrix.md."""
    if row_id is None:
        return False
    return conn.execute(f"SELECT 1 FROM {table} WHERE id=? AND company_id=?", (row_id, cid)).fetchone() is not None

# ─── Dashboard ────────────────────────────────────────────────────────────────

@clinic_bp.route('/dashboard/stats', methods=['GET'])
@mt_login_required
@mt_require_subsystem('clinic')
def dashboard_stats():
    cid = _cid()
    conn = get_clinic_conn()
    cur = conn.cursor()
    today = datetime.now().strftime('%Y-%m-%d')
    cur.execute("SELECT COUNT(*) FROM clinic_appointments WHERE company_id=? AND date(appointment_dt)=?", (cid, today))
    today_appts = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM clinic_appointments WHERE company_id=? AND date(appointment_dt)=? AND status='waiting'", (cid, today))
    waiting = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM clinic_visits WHERE company_id=? AND date(created_at)=? AND status='active'", (cid, today))
    active_visits = cur.fetchone()[0]
    cur.execute("SELECT COALESCE(SUM(amount_paid),0) FROM clinic_payments WHERE company_id=? AND date(created_at)=?", (cid, today))
    revenue = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM clinic_patients WHERE company_id=?", (cid,))
    total_patients = cur.fetchone()[0]
    conn.close()
    return jsonify({'status': 'success', 'data': {
        'today_appointments': today_appts, 'waiting': waiting,
        'active_visits': active_visits, 'today_revenue': revenue,
        'total_patients': total_patients
    }})

# ─── Patients ─────────────────────────────────────────────────────────────────

@clinic_bp.route('/patients', methods=['GET'])
@mt_login_required
@mt_require_subsystem('clinic')
def list_patients():
    cid = _cid()
    conn = get_clinic_conn()
    q = request.args.get('q', '')
    include_archived = request.args.get('include_archived') in ('1', 'true', 'yes')
    arch = '' if include_archived else "AND COALESCE(status,'') != 'archived'"
    if q:
        rows = conn.execute(
            f"SELECT * FROM clinic_patients WHERE company_id=? {arch} AND (name LIKE ? OR phone LIKE ? OR patient_code LIKE ?) ORDER BY id DESC LIMIT 100",
            (cid, f'%{q}%', f'%{q}%', f'%{q}%')
        ).fetchall()
    else:
        rows = conn.execute(f"SELECT * FROM clinic_patients WHERE company_id=? {arch} ORDER BY id DESC LIMIT 100", (cid,)).fetchall()
    conn.close()
    return jsonify({'status': 'success', 'data': [dict(r) for r in rows]})

@clinic_bp.route('/patients', methods=['POST'])
@mt_login_required
@mt_require_subsystem('clinic')
def create_patient():
    data = request.json or {}
    cid = _cid()
    # Phase 3 fix: `name` is NOT NULL in the schema but was never validated
    # here -- an omitted name reached the INSERT and raised an unhandled
    # sqlite3.IntegrityError, caught below, but the exception path never
    # closed `conn` (no finally), leaking an open write transaction that
    # locked clinic.db for every subsequent request. Same class of bug as
    # create_appointment's missing patient_id validation -- fixed the same
    # way, plus the except-block below now also closes the connection
    # defensively for any other unanticipated failure. See
    # docs/migration/clinic-extraction-report.md.
    if not (data.get('name') or '').strip():
        return jsonify({'status': 'error', 'message': 'name is required'}), 400
    conn = None
    try:
        conn = get_clinic_conn()
        code = f'P-{int(time.time())}-{random.randint(100, 999)}'
        cur = conn.cursor()
        cur.execute("""INSERT INTO clinic_patients
            (company_id,patient_code,name,dob,gender,phone,email,address,blood_type,emergency_contact,emergency_phone,notes,created_by)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
            cid, code, data.get('name'), data.get('dob'), data.get('gender'),
            data.get('phone'), data.get('email'), data.get('address'),
            data.get('blood_type'), data.get('emergency_contact'),
            data.get('emergency_phone'), data.get('notes'), _uid()
        ))
        pid = cur.lastrowid
        _audit(conn, 'PatientCreated', 'patient', pid)
        conn.commit(); conn.close()
        _emit('PatientCreated', {'patient_id': pid, 'code': code})
        return jsonify({'status': 'success', 'data': {'id': pid, 'patient_code': code}})
    except Exception as e:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        # Phase 3 fix: the source returned traceback.format_exc() to the client
        # in `detail` -- a stack trace can echo back request values (patient
        # name/DOB/phone/etc. appear as literals in a SQL-bind exception
        # message), which is an information-disclosure risk for a product
        # that handles medical data. Log server-side only; the client gets a
        # generic message. See docs/privacy/clinic-sensitive-data-boundary.md.
        import traceback
        import logging
        logging.getLogger('aura.clinic').error('create_patient failed: %s', traceback.format_exc())
        return jsonify({'status': 'error', 'message': 'Could not create patient record.'}), 500

@clinic_bp.route('/patients/<int:pid>', methods=['GET'])
@mt_login_required
@mt_require_subsystem('clinic')
def get_patient(pid):
    cid = _cid()
    conn = get_clinic_conn()
    p = conn.execute("SELECT * FROM clinic_patients WHERE id=? AND company_id=?", (pid, cid)).fetchone()
    if not p:
        conn.close(); return jsonify({'status': 'error', 'message': 'Not found'}), 404
    visits = conn.execute("SELECT * FROM clinic_visits WHERE patient_id=? AND company_id=? ORDER BY id DESC LIMIT 20", (pid, cid)).fetchall()
    appts = conn.execute("SELECT * FROM clinic_appointments WHERE patient_id=? AND company_id=? ORDER BY id DESC LIMIT 20", (pid, cid)).fetchall()
    _audit(conn, 'PatientFileViewed', 'patient', pid)
    conn.commit(); conn.close()
    return jsonify({'status': 'success', 'data': {
        'patient': dict(p),
        'visits': [dict(r) for r in visits],
        'appointments': [dict(r) for r in appts]
    }})

@clinic_bp.route('/patients/<int:pid>', methods=['PATCH'])
@mt_login_required
@mt_require_subsystem('clinic')
def update_patient(pid):
    data = request.json or {}
    cid = _cid()
    fields = {k: v for k, v in data.items() if k in ['name','dob','gender','phone','email','address','blood_type','emergency_contact','emergency_phone','notes','status']}
    if not fields: return jsonify({'status': 'error', 'message': 'No valid fields'}), 400
    conn = get_clinic_conn()
    sets = ', '.join(f'{k}=?' for k in fields)
    conn.execute(f'UPDATE clinic_patients SET {sets}, updated_by=? WHERE id=? AND company_id=?',
                 list(fields.values()) + [_uid(), pid, cid])
    _audit(conn, 'PatientUpdated', 'patient', pid)
    conn.commit(); conn.close()
    return jsonify({'status': 'success'})

@clinic_bp.route('/patients/<int:pid>', methods=['DELETE'])
@mt_login_required
@mt_require_subsystem('clinic')
def delete_patient(pid):
    """Delete a patient. Default is a SOFT delete (status='archived') so medical and
    financial history is preserved and the action is reversible. Pass ?hard=1 to
    permanently remove the patient AND their clinical records (admin only) — blocked
    if the patient has any invoices/payments, to protect financial history."""
    cid = _cid()
    hard = request.args.get('hard') in ('1', 'true', 'yes')
    conn = get_clinic_conn()
    p = conn.execute("SELECT id FROM clinic_patients WHERE id=? AND company_id=?", (pid, cid)).fetchone()
    if not p:
        conn.close(); return jsonify({'status': 'error', 'message': 'Not found'}), 404

    if hard:
        if session.get('mt_role') != 'admin':
            conn.close()
            return jsonify({'status': 'error', 'message': 'Permanent delete is admin-only.'}), 403
        has_billing = conn.execute(
            "SELECT 1 FROM clinic_invoices WHERE patient_id=? AND company_id=? LIMIT 1", (pid, cid)
        ).fetchone()
        if has_billing:
            conn.close()
            return jsonify({'status': 'error',
                            'message': 'This patient has billing history. Archive instead of permanent delete.'}), 409
        conn.execute("DELETE FROM clinic_followups WHERE patient_id=? AND company_id=?", (pid, cid))
        conn.execute("DELETE FROM clinic_visit_notes WHERE visit_id IN (SELECT id FROM clinic_visits WHERE patient_id=?)", (pid,))
        conn.execute("DELETE FROM clinic_visits WHERE patient_id=? AND company_id=?", (pid, cid))
        conn.execute("DELETE FROM clinic_appointments WHERE patient_id=? AND company_id=?", (pid, cid))
        conn.execute("DELETE FROM clinic_prescriptions WHERE patient_id=? AND company_id=?", (pid, cid))
        conn.execute("DELETE FROM clinic_patients WHERE id=? AND company_id=?", (pid, cid))
        _audit(conn, 'PatientDeleted', 'patient', pid, 'hard')
        conn.commit(); conn.close()
        return jsonify({'status': 'success', 'message': 'Patient permanently deleted.'})

    # Soft delete (default)
    conn.execute("UPDATE clinic_patients SET status='archived', updated_by=? WHERE id=? AND company_id=?",
                 (_uid(), pid, cid))
    _audit(conn, 'PatientArchived', 'patient', pid)
    conn.commit(); conn.close()
    return jsonify({'status': 'success', 'message': 'Patient archived.'})

# ─── Appointments ─────────────────────────────────────────────────────────────

@clinic_bp.route('/appointments', methods=['GET'])
@mt_login_required
@mt_require_subsystem('clinic')
def list_appointments():
    cid = _cid()
    conn = get_clinic_conn()
    q = request.args.get('q', '').strip()
    from_date = request.args.get('from_date', '').strip()
    if q:
        # Search mode: match patient name/phone or appointment reason across ALL dates
        # (booking search, tester request #1). Date filter is ignored while searching.
        like = f'%{q}%'
        rows = conn.execute("""
            SELECT a.*, p.name as patient_name, p.phone as patient_phone
            FROM clinic_appointments a
            LEFT JOIN clinic_patients p ON a.patient_id = p.id
            WHERE a.company_id=? AND (p.name LIKE ? OR p.phone LIKE ? OR a.reason LIKE ?)
            ORDER BY a.appointment_dt DESC LIMIT 200
        """, (cid, like, like, like)).fetchall()
    elif from_date:
        # Upcoming mode (Wave 1A follow-up): the exact-day filter below has no
        # way to see anything beyond a single date at a time -- a real device
        # tester booked an appointment for tomorrow and it was invisible from
        # today's view with no way to browse forward except one day at a
        # time. This returns every appointment from from_date onward.
        rows = conn.execute("""
            SELECT a.*, p.name as patient_name, p.phone as patient_phone
            FROM clinic_appointments a
            LEFT JOIN clinic_patients p ON a.patient_id = p.id
            WHERE a.company_id=? AND date(a.appointment_dt)>=?
            ORDER BY a.appointment_dt LIMIT 200
        """, (cid, from_date)).fetchall()
    else:
        date = request.args.get('date', datetime.now().strftime('%Y-%m-%d'))
        rows = conn.execute("""
            SELECT a.*, p.name as patient_name, p.phone as patient_phone
            FROM clinic_appointments a
            LEFT JOIN clinic_patients p ON a.patient_id = p.id
            WHERE a.company_id=? AND date(a.appointment_dt)=?
            ORDER BY a.appointment_dt
        """, (cid, date)).fetchall()
    conn.close()
    return jsonify({'status': 'success', 'data': [dict(r) for r in rows]})

@clinic_bp.route('/appointments', methods=['POST'])
@mt_login_required
@mt_require_subsystem('clinic')
def create_appointment():
    data = request.json or {}
    cid = _cid()
    # Phase 3 fix: patient_id is NOT NULL in the schema but was never
    # validated here -- an omitted patient_id reached the INSERT and raised
    # an unhandled sqlite3.IntegrityError. Worse, because this function has
    # no try/finally, that crash left `conn` (and its open write
    # transaction) unclosed, which locked the whole clinic.db file for every
    # subsequent request until the connection was eventually garbage
    # collected. No clinic tests existed in source to catch this (Phase 0
    # confirmed zero test coverage for Clinic). Fixed with an upfront
    # validation check -- narrow, backward compatible (never rejects a
    # previously-valid request), and prevents the crash entirely rather
    # than papering over it with a try/finally. See
    # docs/migration/clinic-extraction-report.md.
    if not data.get('patient_id'):
        return jsonify({'status': 'error', 'message': 'patient_id is required'}), 400
    conn = get_clinic_conn()
    # Phase 3 security fix: verify patient_id/doctor_id belong to this
    # company -- see _owned()'s docstring.
    if not _owned(conn, 'clinic_patients', data.get('patient_id'), cid):
        conn.close()
        return jsonify({'status': 'error', 'message': 'Patient not found'}), 404
    if data.get('doctor_id') is not None and not _owned(conn, 'clinic_doctors', data.get('doctor_id'), cid):
        conn.close()
        return jsonify({'status': 'error', 'message': 'Doctor not found'}), 404
    # Double-booking check
    clash = conn.execute("""
        SELECT id FROM clinic_appointments
        WHERE company_id=? AND doctor_id=? AND appointment_dt=? AND status NOT IN ('cancelled','no_show')
    """, (cid, data.get('doctor_id'), data.get('appointment_dt'))).fetchone()
    if clash:
        conn.close()
        return jsonify({'status': 'error', 'message': 'Doctor already has an appointment at this time'}), 409
    cur = conn.cursor()
    cur.execute("""INSERT INTO clinic_appointments
        (company_id,patient_id,doctor_id,appointment_dt,reason,status,created_by)
        VALUES (?,?,?,?,?,?,?)""", (
        cid, data.get('patient_id'), data.get('doctor_id'),
        data.get('appointment_dt'), data.get('reason', ''), 'scheduled', _uid()
    ))
    aid = cur.lastrowid
    _audit(conn, 'AppointmentCreated', 'appointment', aid)
    conn.commit(); conn.close()
    _emit('AppointmentCreated', {'appointment_id': aid})
    return jsonify({'status': 'success', 'data': {'id': aid}})

@clinic_bp.route('/appointments/<int:aid>', methods=['PATCH'])
@mt_login_required
@mt_require_subsystem('clinic')
def update_appointment(aid):
    data = request.json or {}
    cid = _cid()
    conn = get_clinic_conn()
    fields = {k: v for k, v in data.items() if k in ['status','appointment_dt','doctor_id','reason','notes']}
    # Phase 3 security fix: a reassigned doctor_id must belong to this
    # company -- see _owned()'s docstring.
    if fields.get('doctor_id') is not None and not _owned(conn, 'clinic_doctors', fields.get('doctor_id'), cid):
        conn.close()
        return jsonify({'status': 'error', 'message': 'Doctor not found'}), 404
    if fields:
        sets = ', '.join(f'{k}=?' for k in fields)
        conn.execute(f'UPDATE clinic_appointments SET {sets} WHERE id=? AND company_id=?',
                     list(fields.values()) + [aid, cid])
        _audit(conn, f'Appointment_{data.get("status","Updated")}', 'appointment', aid)
        conn.commit()
    conn.close()
    return jsonify({'status': 'success'})

@clinic_bp.route('/appointments/<int:aid>/checkin', methods=['POST'])
@mt_login_required
@mt_require_subsystem('clinic')
def checkin(aid):
    cid = _cid()
    conn = get_clinic_conn()
    conn.execute("UPDATE clinic_appointments SET status='waiting', checked_in_at=CURRENT_TIMESTAMP WHERE id=? AND company_id=?", (aid, cid))
    _audit(conn, 'AppointmentCheckedIn', 'appointment', aid)
    conn.commit(); conn.close()
    _emit('AppointmentCheckedIn', {'appointment_id': aid})
    return jsonify({'status': 'success'})

# ─── Visits ───────────────────────────────────────────────────────────────────

@clinic_bp.route('/visits', methods=['POST'])
@mt_login_required
@mt_require_subsystem('clinic')
def create_visit():
    data = request.json or {}
    cid = _cid()
    conn = get_clinic_conn()
    # Phase 3 security fix: verify every referenced foreign id belongs to
    # this company before linking a new visit to it -- see _owned()'s
    # docstring for the vulnerability this closes.
    if not _owned(conn, 'clinic_patients', data.get('patient_id'), cid):
        conn.close()
        return jsonify({'status': 'error', 'message': 'Patient not found'}), 404
    if data.get('doctor_id') is not None and not _owned(conn, 'clinic_doctors', data.get('doctor_id'), cid):
        conn.close()
        return jsonify({'status': 'error', 'message': 'Doctor not found'}), 404
    if data.get('appointment_id') is not None and not _owned(conn, 'clinic_appointments', data.get('appointment_id'), cid):
        conn.close()
        return jsonify({'status': 'error', 'message': 'Appointment not found'}), 404
    cur = conn.cursor()
    cur.execute("""INSERT INTO clinic_visits
        (company_id,patient_id,doctor_id,appointment_id,status,created_by)
        VALUES (?,?,?,?,?,?)""", (
        cid, data.get('patient_id'), data.get('doctor_id'),
        data.get('appointment_id'), 'active', _uid()
    ))
    vid = cur.lastrowid
    if data.get('appointment_id'):
        conn.execute("UPDATE clinic_appointments SET status='in_progress' WHERE id=?", (data['appointment_id'],))
    _audit(conn, 'VisitStarted', 'visit', vid)
    conn.commit(); conn.close()
    _emit('VisitStarted', {'visit_id': vid})
    return jsonify({'status': 'success', 'data': {'id': vid}})

@clinic_bp.route('/visits/<int:vid>', methods=['GET'])
@mt_login_required
@mt_require_subsystem('clinic')
def get_visit(vid):
    cid = _cid()
    conn = get_clinic_conn()
    v = conn.execute("SELECT * FROM clinic_visits WHERE id=? AND company_id=?", (vid, cid)).fetchone()
    if not v: conn.close(); return jsonify({'status': 'error', 'message': 'Not found'}), 404
    notes = conn.execute("SELECT * FROM clinic_visit_notes WHERE visit_id=? ORDER BY id", (vid,)).fetchall()
    _audit(conn, 'VisitViewed', 'visit', vid)
    conn.commit(); conn.close()
    return jsonify({'status': 'success', 'data': {'visit': dict(v), 'notes': [dict(n) for n in notes]}})

@clinic_bp.route('/visits/<int:vid>', methods=['PATCH'])
@mt_login_required
@mt_require_subsystem('clinic')
@require_clinic_role('doctor')   # diagnosis/treatment is doctor (or admin) work
def update_visit(vid):
    data = request.json or {}
    cid = _cid()
    conn = get_clinic_conn()
    fields = {k: v for k, v in data.items() if k in ['status','follow_up_date','diagnosis','treatment']}
    if fields:
        sets = ', '.join(f'{k}=?' for k in fields)
        if data.get('status') == 'completed':
            conn.execute(f'UPDATE clinic_visits SET {sets}, completed_at=CURRENT_TIMESTAMP WHERE id=? AND company_id=?',
                         list(fields.values()) + [vid, cid])
            _emit('VisitCompleted', {'visit_id': vid})
        else:
            conn.execute(f'UPDATE clinic_visits SET {sets} WHERE id=? AND company_id=?',
                         list(fields.values()) + [vid, cid])
        _audit(conn, 'VisitUpdated', 'visit', vid)
        conn.commit()
    conn.close()
    return jsonify({'status': 'success'})

@clinic_bp.route('/visits/<int:vid>/notes', methods=['POST'])
@mt_login_required
@mt_require_subsystem('clinic')
@require_clinic_role('doctor')   # clinical notes — doctor (or admin)
def add_note(vid):
    data = request.json or {}
    cid = _cid()
    conn = get_clinic_conn()
    # Phase 3 security fix: `vid` came straight from the URL with no check
    # that the visit belongs to this company -- see _owned()'s docstring.
    if not _owned(conn, 'clinic_visits', vid, cid):
        conn.close()
        return jsonify({'status': 'error', 'message': 'Visit not found'}), 404
    cur = conn.cursor()
    cur.execute("INSERT INTO clinic_visit_notes (visit_id,company_id,note_type,content,created_by) VALUES (?,?,?,?,?)",
                (vid, cid, data.get('note_type', 'general'), data.get('content', ''), _uid()))
    nid = cur.lastrowid
    _audit(conn, 'NoteAdded', 'visit_note', nid, f'visit:{vid}')
    conn.commit(); conn.close()
    return jsonify({'status': 'success', 'data': {'id': nid}})

# ─── Follow-up Sheet ──────────────────────────────────────────────────────────
# A chronological, append-only sheet per patient. Reception/clinical staff add a
# dated entry (vitals + progress + notes) on each follow-up visit. (Tester #2.)

@clinic_bp.route('/patients/<int:pid>/followups', methods=['GET'])
@mt_login_required
@mt_require_subsystem('clinic')
def list_followups(pid):
    cid = _cid()
    conn = get_clinic_conn()
    rows = conn.execute(
        "SELECT * FROM clinic_followups WHERE patient_id=? AND company_id=? ORDER BY id DESC",
        (pid, cid)
    ).fetchall()
    conn.close()
    return jsonify({'status': 'success', 'data': [dict(r) for r in rows]})

@clinic_bp.route('/patients/<int:pid>/followups', methods=['POST'])
@mt_login_required
@mt_require_subsystem('clinic')
@require_clinic_role('doctor')   # clinical follow-up content — doctor (or admin)
def add_followup(pid):
    data = request.json or {}
    cid = _cid()
    conn = get_clinic_conn()
    # Phase 3 security fix: `pid` came straight from the URL with no check
    # that the patient belongs to this company -- see _owned()'s docstring.
    if not _owned(conn, 'clinic_patients', pid, cid):
        conn.close()
        return jsonify({'status': 'error', 'message': 'Patient not found'}), 404
    if data.get('visit_id') is not None and not _owned(conn, 'clinic_visits', data.get('visit_id'), cid):
        conn.close()
        return jsonify({'status': 'error', 'message': 'Visit not found'}), 404
    cur = conn.cursor()
    cur.execute("""INSERT INTO clinic_followups
        (company_id,patient_id,visit_id,followup_date,weight,blood_pressure,temperature,progress,notes,created_by)
        VALUES (?,?,?,?,?,?,?,?,?,?)""", (
        cid, pid, data.get('visit_id'),
        data.get('followup_date') or datetime.now().strftime('%Y-%m-%d'),
        data.get('weight', ''), data.get('blood_pressure', ''), data.get('temperature', ''),
        data.get('progress', ''), data.get('notes', ''), _uid()
    ))
    fid = cur.lastrowid
    _audit(conn, 'FollowupAdded', 'followup', fid, f'patient:{pid}')
    conn.commit(); conn.close()
    return jsonify({'status': 'success', 'data': {'id': fid}})

@clinic_bp.route('/followups/<int:fid>', methods=['DELETE'])
@mt_login_required
@mt_require_subsystem('clinic')
@require_clinic_role('doctor')
def delete_followup(fid):
    cid = _cid()
    conn = get_clinic_conn()
    conn.execute("DELETE FROM clinic_followups WHERE id=? AND company_id=?", (fid, cid))
    _audit(conn, 'FollowupDeleted', 'followup', fid)
    conn.commit(); conn.close()
    return jsonify({'status': 'success'})

# ─── Doctors ──────────────────────────────────────────────────────────────────

@clinic_bp.route('/doctors', methods=['GET'])
@mt_login_required
@mt_require_subsystem('clinic')
def list_doctors():
    cid = _cid()
    conn = get_clinic_conn()
    rows = conn.execute(
        "SELECT * FROM clinic_doctors WHERE company_id=? ORDER BY name", (cid,)
    ).fetchall()
    conn.close()
    return jsonify({'status': 'success', 'data': [dict(r) for r in rows]})

@clinic_bp.route('/doctors', methods=['POST'])
@mt_login_required
@mt_require_subsystem('clinic')
def create_doctor():
    data = request.json or {}
    cid = _cid()
    if not data.get('name'):
        return jsonify({'status': 'error', 'message': 'Name is required'}), 400
    conn = get_clinic_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO clinic_doctors (company_id,name,specialty,phone,email,status) VALUES (?,?,?,?,?,?)",
        (cid, data['name'], data.get('specialty',''), data.get('phone',''), data.get('email',''), 'active')
    )
    did = cur.lastrowid
    _audit(conn, 'DoctorAdded', 'doctor', did)
    conn.commit(); conn.close()
    return jsonify({'status': 'success', 'data': {'id': did}})

@clinic_bp.route('/doctors/<int:did>', methods=['PATCH'])
@mt_login_required
@mt_require_subsystem('clinic')
def update_doctor(did):
    data = request.json or {}
    cid = _cid()
    fields = {k: v for k, v in data.items() if k in ['name','specialty','phone','email','status']}
    if not fields:
        return jsonify({'status': 'error', 'message': 'No valid fields'}), 400
    conn = get_clinic_conn()
    sets = ', '.join(f'{k}=?' for k in fields)
    conn.execute(f'UPDATE clinic_doctors SET {sets} WHERE id=? AND company_id=?',
                 list(fields.values()) + [did, cid])
    _audit(conn, 'DoctorUpdated', 'doctor', did)
    conn.commit(); conn.close()
    return jsonify({'status': 'success'})

# ─── Services ─────────────────────────────────────────────────────────────────

@clinic_bp.route('/services', methods=['GET'])
@mt_login_required
@mt_require_subsystem('clinic')
def list_services():
    cid = _cid()
    conn = get_clinic_conn()
    rows = conn.execute("SELECT * FROM clinic_services WHERE company_id=? AND status='active' ORDER BY name", (cid,)).fetchall()
    conn.close()
    return jsonify({'status': 'success', 'data': [dict(r) for r in rows]})

@clinic_bp.route('/services', methods=['POST'])
@mt_login_required
@mt_require_subsystem('clinic')
def create_service():
    data = request.json or {}
    cid = _cid()
    conn = get_clinic_conn()
    cur = conn.cursor()
    cur.execute("INSERT INTO clinic_services (company_id,name,description,price,category,status) VALUES (?,?,?,?,?,?)",
                (cid, data.get('name'), data.get('description',''), data.get('price',0), data.get('category',''), 'active'))
    sid = cur.lastrowid
    conn.commit(); conn.close()
    return jsonify({'status': 'success', 'data': {'id': sid}})

# ─── Lab Expenses ─────────────────────────────────────────────────────────────
# Costs the clinic pays to external labs. Recorded here AND posted to Accounting as
# an expense transaction so it shows up in financial data. (Tester #3.)

@clinic_bp.route('/lab-expenses', methods=['GET'])
@mt_login_required
@mt_require_subsystem('clinic')
def list_lab_expenses():
    cid = _cid()
    conn = get_clinic_conn()
    rows = conn.execute("""SELECT le.*, p.name as patient_name
        FROM clinic_lab_expenses le LEFT JOIN clinic_patients p ON le.patient_id=p.id
        WHERE le.company_id=? ORDER BY le.id DESC LIMIT 200""", (cid,)).fetchall()
    conn.close()
    return jsonify({'status': 'success', 'data': [dict(r) for r in rows]})

@clinic_bp.route('/lab-expenses', methods=['POST'])
@mt_login_required
@mt_require_subsystem('clinic')
def create_lab_expense():
    data = request.json or {}
    cid = _cid()
    conn = get_clinic_conn()
    # Phase 3 security fix: patient_id is optional here, but when provided
    # it must belong to this company -- see _owned()'s docstring.
    if data.get('patient_id') is not None and not _owned(conn, 'clinic_patients', data.get('patient_id'), cid):
        conn.close()
        return jsonify({'status': 'error', 'message': 'Patient not found'}), 404
    cur = conn.cursor()
    amount = float(data.get('amount') or 0)
    exp_date = data.get('expense_date') or datetime.now().strftime('%Y-%m-%d')
    cur.execute("""INSERT INTO clinic_lab_expenses
        (company_id,patient_id,lab_name,test_name,amount,expense_date,notes,created_by)
        VALUES (?,?,?,?,?,?,?,?)""", (
        cid, data.get('patient_id'), data.get('lab_name', ''), data.get('test_name', ''),
        amount, exp_date, data.get('notes', ''), _uid()
    ))
    le_id = cur.lastrowid
    _audit(conn, 'LabExpenseRecorded', 'lab_expense', le_id)
    conn.commit(); conn.close()
    # Post to Accounting as an expense so it appears in financial data (best-effort).
    try:
        from database.subsystem_db import sub_create, get_accounting_conn
        sub_create(get_accounting_conn, 'transactions', {
            'date': exp_date,
            'description': f"Clinic Lab: {data.get('lab_name','')} — {data.get('test_name','')}".strip(' —'),
            'amount': amount,
            'type': 'expense',
            'category': 'Lab',
            'status': 'posted',
            'reference': f'CLINIC-LAB-{le_id}',
        })
    except Exception as e:
        # Phase 4I: never interpolate the raw exception -- lab_name/test_name
        # can be sensitive (a lab test name can itself reveal a medical
        # condition), and a SQL-bind error can embed request values as
        # literals in its message (the same class of leak already fixed for
        # create_patient/create_invoice/record_payment's own primary
        # exception handlers). Log only the exception type; on Android this
        # print() would otherwise be forwarded to Logcat by Chaquopy.
        import logging
        logging.getLogger('aura.clinic').warning(
            'lab expense accounting sync skipped: %s', type(e).__name__)
    return jsonify({'status': 'success', 'data': {'id': le_id}})

@clinic_bp.route('/lab-expenses/<int:le_id>', methods=['DELETE'])
@mt_login_required
@mt_require_subsystem('clinic')
def delete_lab_expense(le_id):
    cid = _cid()
    conn = get_clinic_conn()
    conn.execute("DELETE FROM clinic_lab_expenses WHERE id=? AND company_id=?", (le_id, cid))
    _audit(conn, 'LabExpenseDeleted', 'lab_expense', le_id)
    conn.commit(); conn.close()
    return jsonify({'status': 'success'})

# ─── Billing ──────────────────────────────────────────────────────────────────

@clinic_bp.route('/invoices', methods=['POST'])
@mt_login_required
@mt_require_subsystem('clinic')
def create_invoice():
    data = request.json or {}
    cid = _cid()
    conn = get_clinic_conn()
    # Phase 3 security fix: patient_id/visit_id/prescription_id are optional
    # here, but when provided they must belong to this company -- see
    # _owned()'s docstring.
    for _field, _table, _label in (
        ('patient_id', 'clinic_patients', 'Patient'),
        ('visit_id', 'clinic_visits', 'Visit'),
        ('prescription_id', 'clinic_prescriptions', 'Prescription'),
    ):
        if data.get(_field) is not None and not _owned(conn, _table, data.get(_field), cid):
            conn.close()
            return jsonify({'status': 'error', 'message': f'{_label} not found'}), 404
    cur = conn.cursor()
    try:
        # Wave 0 (AUDIT-018): header insert + line-item inserts + the
        # prescription back-link now happen inside one transaction -- a
        # mid-loop failure previously could leave an invoice header with no
        # line items and no rollback.
        conn.execute("BEGIN IMMEDIATE")
        # Unique even when several invoices are created within the same second
        # (invoice_number is UNIQUE — a bare int(time.time()) would collide).
        inv_no = f'INV-C-{int(time.time())}-{random.randint(100, 999)}'
        items = data.get('items', [])
        subtotal = sum(i.get('qty', 1) * i.get('unit_price', 0) for i in items)
        # Discount applied before tax (tax-after-discount), mirroring the POS fix.
        discount = float(data.get('discount') or 0)
        if discount < 0: discount = 0
        if discount > subtotal: discount = subtotal
        taxable = subtotal - discount
        tax = round(taxable * float(data.get('tax_rate') or 0), 2)
        total = round(taxable + tax, 2)
        cur.execute("""INSERT INTO clinic_invoices
            (company_id,invoice_number,patient_id,visit_id,subtotal,discount,tax,total,status,notes,prescription_id,created_by)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (cid, inv_no, data.get('patient_id'), data.get('visit_id'), subtotal, discount, tax, total,
             'unpaid', data.get('notes', ''), data.get('prescription_id'), _uid()))
        inv_id = cur.lastrowid
        # If this invoice was generated from a prescription, link it back (tester #13).
        if data.get('prescription_id'):
            conn.execute("UPDATE clinic_prescriptions SET invoice_id=? WHERE id=? AND company_id=?",
                         (inv_id, data['prescription_id'], cid))
        for item in items:
            cur.execute("INSERT INTO clinic_invoice_items (invoice_id,service_id,description,qty,unit_price,line_total) VALUES (?,?,?,?,?,?)",
                        (inv_id, item.get('service_id'), item.get('description',''), item.get('qty',1),
                         item.get('unit_price',0), item.get('qty',1)*item.get('unit_price',0)))
        _audit(conn, 'InvoiceCreated', 'invoice', inv_id)
        conn.commit()
    except Exception as e:
        conn.rollback(); conn.close()
        # Never echo the raw exception to the client -- a SQL-bind error can
        # embed request values (patient name/notes/etc) as literals in its
        # message, which is exactly the information-disclosure class the
        # Phase 3 fix to create_patient() already remediated (see that
        # function's own comment). Log server-side only.
        import logging
        logging.getLogger('aura.clinic').error('create_invoice failed: %s', e)
        return jsonify({'status': 'error', 'message': 'Could not create invoice.'}), 500
    conn.close()
    _emit('ClinicInvoiceIssued', {'invoice_id': inv_id, 'total': total})
    # Mirror into the Accounting invoice system so clinic billing also lands in the
    # company books — this is the "linked to both invoice systems" requirement (#13);
    # a prescription-generated invoice therefore appears in clinic billing AND
    # Accounting. Best-effort: never block the clinic invoice if accounting is down.
    try:
        from database.subsystem_db import sub_create, get_accounting_conn
        _pname = ''
        try:
            _c = get_clinic_conn()
            _r = _c.execute("SELECT name FROM clinic_patients WHERE id=?", (data.get('patient_id'),)).fetchone()
            _pname = _r['name'] if _r else ''
            _c.close()
        except Exception:
            pass
        sub_create(get_accounting_conn, 'invoices', {
            'invoice_number': inv_no,
            'client_name':    _pname or 'Clinic Patient',
            'amount':         taxable,
            'tax':            tax,
            'total':          total,
            'status':         'unpaid',
            'due_date':       '',
            'notes':          ('Clinic billing' + (f' (Rx #{data.get("prescription_id")})' if data.get('prescription_id') else '')),
        })
        _c = get_clinic_conn()
        _c.execute("UPDATE clinic_invoices SET accounting_synced=1 WHERE id=?", (inv_id,))
        _c.commit(); _c.close()
    except Exception as e:
        # Phase 4I: never interpolate the raw exception -- this block just
        # looked up the patient's real name into `_pname`, so a SQL-bind
        # error embedding request values in its message would leak PII into
        # this log line (the same class of leak already fixed for
        # create_patient/create_invoice/record_payment's own primary
        # exception handlers). Log only the exception type; on Android this
        # print() would otherwise be forwarded to Logcat by Chaquopy.
        import logging
        logging.getLogger('aura.clinic').warning(
            'invoice accounting sync skipped: %s', type(e).__name__)
    return jsonify({'status': 'success', 'data': {'id': inv_id, 'invoice_number': inv_no, 'total': total}})

@clinic_bp.route('/invoices', methods=['GET'])
@mt_login_required
@mt_require_subsystem('clinic')
def list_invoices():
    cid = _cid()
    conn = get_clinic_conn()
    rows = conn.execute("""SELECT i.*, p.name as patient_name
        FROM clinic_invoices i LEFT JOIN clinic_patients p ON i.patient_id=p.id
        WHERE i.company_id=? ORDER BY i.id DESC LIMIT 100""", (cid,)).fetchall()
    conn.close()
    return jsonify({'status': 'success', 'data': [dict(r) for r in rows]})

@clinic_bp.route('/invoices/<int:inv_id>', methods=['GET'])
@mt_login_required
@mt_require_subsystem('clinic')
def get_invoice(inv_id):
    cid = _cid()
    conn = get_clinic_conn()
    inv = conn.execute("SELECT * FROM clinic_invoices WHERE id=? AND company_id=?", (inv_id, cid)).fetchone()
    if not inv: conn.close(); return jsonify({'status': 'error', 'message': 'Not found'}), 404
    items = conn.execute("SELECT * FROM clinic_invoice_items WHERE invoice_id=?", (inv_id,)).fetchall()
    payments = conn.execute("SELECT * FROM clinic_payments WHERE invoice_id=?", (inv_id,)).fetchall()
    conn.close()
    return jsonify({'status': 'success', 'data': {
        'invoice': dict(inv), 'items': [dict(r) for r in items], 'payments': [dict(r) for r in payments]
    }})

@clinic_bp.route('/payments', methods=['POST'])
@mt_login_required
@mt_require_subsystem('clinic')
def record_payment():
    """Server-validated, idempotent payment recording (Wave 0, AUDIT-011/AUDIT-012).

    amount must parse as a positive Decimal and must not exceed the invoice's
    current outstanding balance -- this product has no customer-credit ledger,
    so an overpayment is rejected outright rather than silently accepted (see
    docs/corrections/wave0/clinic-payment-correction.md). An idempotency_key
    makes a retried/double-submitted request return the original payment
    instead of creating a second one. The payment insert and the invoice
    status/amount_paid update happen inside one transaction (AUDIT-018) --
    previously neither validation nor atomicity existed here at all.
    """
    data = request.json or {}
    cid = _cid()
    invoice_id = data.get('invoice_id')
    conn = get_clinic_conn()
    # Phase 3 security fix: this was the worst instance of the tenant
    # -isolation gap described in _owned()'s docstring -- the SELECT and
    # UPDATE below had NO company_id filter at all, so any authenticated
    # user of ANY company could read another company's invoice total and
    # mark it paid. Now verified upfront.
    if not _owned(conn, 'clinic_invoices', invoice_id, cid):
        conn.close()
        return jsonify({'status': 'error', 'message': 'Invoice not found'}), 404

    idem = data.get('idempotency_key')
    if idem:
        # company_id-scoped: an unscoped lookup here would let a caller who
        # somehow knew or guessed another company's idempotency_key read
        # back that company's payment id/invoice status.
        existing = conn.execute(
            "SELECT id FROM clinic_payments WHERE idempotency_key=? AND company_id=?", (idem, cid)
        ).fetchone()
        if existing:
            inv_now = conn.execute("SELECT status FROM clinic_invoices WHERE id=? AND company_id=?",
                                    (invoice_id, cid)).fetchone()
            conn.close()
            return jsonify({'status': 'success', 'data': {
                'id': existing['id'], 'invoice_status': inv_now['status'] if inv_now else None,
            }})

    try:
        amount_dec = Decimal(str(data.get('amount')))
    except Exception:
        conn.close()
        return jsonify({'status': 'error', 'message': 'Payment amount must be a number.'}), 400
    if amount_dec <= 0:
        conn.close()
        return jsonify({'status': 'error', 'message': 'Payment amount must be greater than zero.'}), 400
    amount = float(amount_dec.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))

    try:
        # BEGIN IMMEDIATE: two payment requests against the same invoice
        # racing each other must not both read the same "outstanding
        # balance" snapshot before either has committed.
        conn.execute("BEGIN IMMEDIATE")
        inv = conn.execute("SELECT total FROM clinic_invoices WHERE id=? AND company_id=?", (invoice_id, cid)).fetchone()
        if not inv:
            conn.rollback(); conn.close()
            return jsonify({'status': 'error', 'message': 'Invoice not found'}), 404

        already_paid = conn.execute(
            "SELECT COALESCE(SUM(amount_paid),0) FROM clinic_payments WHERE invoice_id=? AND company_id=?",
            (invoice_id, cid)
        ).fetchone()[0]
        total_dec = Decimal(str(inv['total']))
        outstanding = float((total_dec - Decimal(str(already_paid))).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))
        if amount > outstanding + 0.005:
            conn.rollback(); conn.close()
            return jsonify({'status': 'error', 'message':
                f'Payment of {amount:.2f} exceeds the outstanding balance of {outstanding:.2f}. '
                'This product has no customer-credit ledger, so overpayment cannot be accepted.'}), 400

        cur = conn.cursor()
        cur.execute(
            "INSERT INTO clinic_payments (company_id,invoice_id,amount_paid,method,reference,created_by,idempotency_key) "
            "VALUES (?,?,?,?,?,?,?)",
            (cid, invoice_id, amount, data.get('method', 'cash'), data.get('reference', ''), _uid(), idem))
        pay_id = cur.lastrowid
        paid = float((Decimal(str(already_paid)) + Decimal(str(amount))).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))
        status = 'paid' if paid >= float(total_dec) - 0.005 else 'partial'
        conn.execute("UPDATE clinic_invoices SET status=?, amount_paid=? WHERE id=? AND company_id=?",
                     (status, paid, invoice_id, cid))
        _audit(conn, 'PaymentRecorded', 'payment', pay_id)
        conn.commit()
    except Exception as e:
        conn.rollback(); conn.close()
        # Same information-disclosure concern as create_invoice()'s handler
        # above -- log server-side only, never echo the raw exception.
        import logging
        logging.getLogger('aura.clinic').error('record_payment failed: %s', e)
        return jsonify({'status': 'error', 'message': 'Could not record payment.'}), 500

    conn.close()
    _emit('ClinicPaymentReceived', {'payment_id': pay_id, 'amount': amount})
    return jsonify({'status': 'success', 'data': {
        'id': pay_id, 'invoice_id': invoice_id, 'amount': amount, 'total_paid': paid,
        'outstanding_balance': round(float(total_dec) - paid, 2),
        'invoice_status': status, 'idempotency_key': idem,
    }})

# ─── Prescriptions ────────────────────────────────────────────────────────────

@clinic_bp.route('/prescriptions', methods=['POST'])
@mt_login_required
@mt_require_subsystem('clinic')
@require_clinic_role('doctor')   # prescribing is doctor (or admin) only
def create_prescription():
    data = request.json or {}
    cid = _cid()
    conn = get_clinic_conn()
    # Phase 3 security fix: verify patient_id (required) and visit_id
    # (optional) belong to this company -- see _owned()'s docstring.
    if not _owned(conn, 'clinic_patients', data.get('patient_id'), cid):
        conn.close()
        return jsonify({'status': 'error', 'message': 'Patient not found'}), 404
    if data.get('visit_id') is not None and not _owned(conn, 'clinic_visits', data.get('visit_id'), cid):
        conn.close()
        return jsonify({'status': 'error', 'message': 'Visit not found'}), 404
    cur = conn.cursor()
    # Phase 3 fix: the source wrote str(list) (Python repr, e.g. single-quoted)
    # instead of real JSON -- the frontend already has to work around this
    # with `items_json.replace(/'/g,'"')` before JSON.parse(), which silently
    # corrupts any medication name containing an apostrophe (e.g. "Children's
    # Tylenol"). json.dumps() produces valid JSON with no single quotes, so
    # the existing frontend workaround remains a harmless no-op against it --
    # this is backward compatible, not a breaking format change.
    import json as _json
    cur.execute("INSERT INTO clinic_prescriptions (company_id,patient_id,visit_id,items_json,notes,created_by) VALUES (?,?,?,?,?,?)",
                (cid, data.get('patient_id'), data.get('visit_id'),
                 _json.dumps(data.get('items', [])), data.get('notes',''), _uid()))
    prx_id = cur.lastrowid
    _audit(conn, 'PrescriptionRecorded', 'prescription', prx_id)
    conn.commit(); conn.close()
    _emit('PrescriptionRecorded', {'prescription_id': prx_id})
    return jsonify({'status': 'success', 'data': {'id': prx_id}})

@clinic_bp.route('/prescriptions', methods=['GET'])
@mt_login_required
@mt_require_subsystem('clinic')
def list_prescriptions():
    cid = _cid()
    pid = request.args.get('patient_id')
    conn = get_clinic_conn()
    if pid:
        rows = conn.execute("SELECT * FROM clinic_prescriptions WHERE company_id=? AND patient_id=? ORDER BY id DESC", (cid, pid)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM clinic_prescriptions WHERE company_id=? ORDER BY id DESC LIMIT 50", (cid,)).fetchall()
    conn.close()
    return jsonify({'status': 'success', 'data': [dict(r) for r in rows]})

# ─── Reports ──────────────────────────────────────────────────────────────────

@clinic_bp.route('/reports/appointments', methods=['GET'])
@mt_login_required
@mt_require_subsystem('clinic')
def report_appointments():
    cid = _cid()
    conn = get_clinic_conn()
    start = request.args.get('start', (datetime.now()-timedelta(days=30)).strftime('%Y-%m-%d'))
    end = request.args.get('end', datetime.now().strftime('%Y-%m-%d'))
    rows = conn.execute("""
        SELECT date(appointment_dt) as day, status, COUNT(*) as count
        FROM clinic_appointments WHERE company_id=? AND date(appointment_dt) BETWEEN ? AND ?
        GROUP BY day, status ORDER BY day
    """, (cid, start, end)).fetchall()
    conn.close()
    return jsonify({'status': 'success', 'data': [dict(r) for r in rows]})

@clinic_bp.route('/reports/revenue', methods=['GET'])
@mt_login_required
@mt_require_subsystem('clinic')
def report_revenue():
    cid = _cid()
    conn = get_clinic_conn()
    start = request.args.get('start', (datetime.now()-timedelta(days=30)).strftime('%Y-%m-%d'))
    end = request.args.get('end', datetime.now().strftime('%Y-%m-%d'))
    rows = conn.execute("""
        SELECT date(created_at) as day, SUM(amount_paid) as revenue, COUNT(*) as transactions
        FROM clinic_payments WHERE company_id=? AND date(created_at) BETWEEN ? AND ?
        GROUP BY day ORDER BY day
    """, (cid, start, end)).fetchall()
    conn.close()
    return jsonify({'status': 'success', 'data': [dict(r) for r in rows]})

@clinic_bp.route('/reports/audit', methods=['GET'])
@mt_login_required
@mt_require_subsystem('clinic')
def report_audit():
    cid = _cid()
    conn = get_clinic_conn()
    rows = conn.execute(
        "SELECT * FROM clinic_audit_log WHERE company_id=? ORDER BY id DESC LIMIT 200", (cid,)
    ).fetchall()
    conn.close()
    return jsonify({'status': 'success', 'data': [dict(r) for r in rows]})

# ─── Demo Seed/Wipe ───────────────────────────────────────────────────────────

# ── Demo Seed/Wipe -- Phase 3 security hardening ──────────────────────────────
# The source implementation gated these routes with ONLY @mt_login_required +
# @mt_require_subsystem('clinic') -- no demo-mode check, no admin-only check,
# no confirmation token, AND the wipe statements had no `WHERE company_id=?`
# at all. That means, as shipped in source, ANY authenticated clinic user
# (secretary, doctor, anyone) could permanently delete EVERY company's
# patients/appointments/invoices/prescriptions with one unconfirmed request
# -- in production, not just demo builds. This mirrors the exact hardening
# Retail already received in its own Phase 1 remediation (see
# docs/retail/RETAIL_SECURITY_PHASE_1.md, carried into
# products/retail/backend/api/retail_api.py's demo_wipe/demo_seed): a
# runtime-mode gate, an admin-only check, an explicit per-company
# confirmation token, and every statement scoped to the caller's own
# company_id inside one rolled-back-on-failure transaction. See
# docs/migration/clinic-extraction-report.md and
# docs/security/clinic-rbac-matrix.md.

def _require_clinic_demo_mode():
    from commercial_runtime.security.modes import clinic_demo_mode_enabled
    if not clinic_demo_mode_enabled():
        return jsonify({'error': 'Demo reset is not available in this build.'}), 404
    return None


def _require_company_admin():
    if session.get('mt_role') != 'admin':
        return jsonify({'error': 'Administrator permission required.'}), 403
    return None


def _require_confirmation(expected_prefix, cid):
    data = request.get_json(silent=True) or {}
    expected = f'{expected_prefix}-{cid}'
    if (data.get('confirm') or '').strip() != expected:
        return jsonify({
            'error': f'Confirmation required. Resend the request with {{"confirm": "{expected}"}}.'
        }), 400
    return None


# (table, statement) pairs, parent-before-child order preserved from the
# original code. clinic_invoice_items has no company_id column of its own --
# it is scoped only through its parent invoice's company_id, via a subquery.
_WIPE_STATEMENTS = (
    ('clinic_payments', 'DELETE FROM clinic_payments WHERE company_id=?'),
    ('clinic_invoice_items', 'DELETE FROM clinic_invoice_items WHERE invoice_id IN (SELECT id FROM clinic_invoices WHERE company_id=?)'),
    ('clinic_invoices', 'DELETE FROM clinic_invoices WHERE company_id=?'),
    ('clinic_visit_notes', 'DELETE FROM clinic_visit_notes WHERE company_id=?'),
    ('clinic_visits', 'DELETE FROM clinic_visits WHERE company_id=?'),
    ('clinic_appointments', 'DELETE FROM clinic_appointments WHERE company_id=?'),
    ('clinic_prescriptions', 'DELETE FROM clinic_prescriptions WHERE company_id=?'),
    ('clinic_patients', 'DELETE FROM clinic_patients WHERE company_id=?'),
    ('clinic_services', 'DELETE FROM clinic_services WHERE company_id=?'),
    ('clinic_audit_log', 'DELETE FROM clinic_audit_log WHERE company_id=?'),
)


@clinic_bp.route('/demo-wipe', methods=['DELETE'])
@mt_login_required
@mt_require_subsystem('clinic')
def demo_wipe():
    for guard in (_require_clinic_demo_mode(), _require_company_admin(), _require_confirmation('WIPE', _cid())):
        if guard is not None:
            return guard

    cid = _cid()
    conn = get_clinic_conn()
    try:
        conn.execute("BEGIN TRANSACTION")
        for _table, stmt in _WIPE_STATEMENTS:
            conn.execute(stmt, (cid,))
        conn.commit()
    except Exception as e:
        conn.rollback()
        from commercial_runtime.security.audit import record as _sec_audit, SECURITY_CONFIG_FAILURE
        _sec_audit(cid, _uid(), SECURITY_CONFIG_FAILURE, context={'action': 'clinic_demo_wipe', 'error': type(e).__name__})
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()

    from commercial_runtime.security.audit import record as _sec_audit, DEMO_RESET_EXECUTED
    _sec_audit(cid, _uid(), DEMO_RESET_EXECUTED, context={'action': 'clinic_demo_wipe'})
    return jsonify({'status': 'success', 'message': 'Clinic data wiped.'})


@clinic_bp.route('/demo-seed', methods=['POST'])
@mt_login_required
@mt_require_subsystem('clinic')
def demo_seed():
    for guard in (_require_clinic_demo_mode(), _require_company_admin(), _require_confirmation('SEED', _cid())):
        if guard is not None:
            return guard

    from database.schema import _seed_clinic
    cid = _cid()
    conn = get_clinic_conn()
    try:
        conn.execute("BEGIN TRANSACTION")
        for _table, stmt in _WIPE_STATEMENTS:
            conn.execute(stmt, (cid,))
        _seed_clinic(conn, conn.cursor())
        conn.commit()
    except Exception as e:
        conn.rollback()
        from commercial_runtime.security.audit import record as _sec_audit, SECURITY_CONFIG_FAILURE
        _sec_audit(cid, _uid(), SECURITY_CONFIG_FAILURE, context={'action': 'clinic_demo_seed', 'error': type(e).__name__})
        return jsonify({'status': 'error', 'message': str(e)}), 500
    finally:
        conn.close()

    from commercial_runtime.security.audit import record as _sec_audit, DEMO_RESET_EXECUTED
    _sec_audit(cid, _uid(), DEMO_RESET_EXECUTED, context={'action': 'clinic_demo_seed'})
    return jsonify({'status': 'success', 'message': 'Clinic seeded with demo data.'})
