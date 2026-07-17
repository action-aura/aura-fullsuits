"""
Aura FullSuits -- backup/restore HTTP surface (Wave 0, AUDIT-019).

Thin admin-only wrapper around commercial_runtime.backup.service. One
blueprint factory shared by both products so the endpoint shape, auth
gating, and error handling stay identical -- only `product_code` and the
app's own `database_dir` differ per product.

Endpoints (all require an authenticated session with role == 'admin'):
  POST /api/backup/create            -> create a backup, returns manifest
  GET  /api/backup/list              -> list backups in the default directory
  GET  /api/backup/download/<name>   -> download a backup file
  POST /api/backup/restore           -> restore from an uploaded file or a
                                         filename already in the default dir
"""
import os
import logging

from flask import Blueprint, request, jsonify, session, send_from_directory
from werkzeug.utils import secure_filename

from commercial_runtime.backup.service import (
    create_backup, restore_backup, BackupError, is_safe_backup_dir,
)

log = logging.getLogger('aura.backup')


def _require_admin():
    # Deliberately no demo-mode bypass here, unlike mt_login_required's
    # `session.get('is_demo_mode')` carve-out -- that flag is never actually
    # set anywhere in this codebase today (it's vestigial from the source
    # monolith), but backup/restore is destructive enough (restore replaces
    # live databases) that it must always require a real authenticated admin
    # session, even if some future code path starts setting that flag for
    # convenience on lower-risk routes.
    if 'mt_user_id' not in session:
        return jsonify({'error': 'Authentication required', 'code': 401}), 401
    if session.get('mt_role') != 'admin':
        return jsonify({'error': 'Backup/restore requires an administrator account', 'code': 403}), 403
    return None


def make_backup_blueprint(product_code, database_dir, app_version, default_backup_dir=None):
    """
    `database_dir` is the product's own `<app_data>/database` path (the
    parent of `registry.db` and `subsystems/`) -- callers pass their own
    `config.DATABASE_DIR` so this module never re-derives app-data location
    on its own.
    """
    app_data_dir = os.path.dirname(database_dir)
    backup_dir = default_backup_dir or os.environ.get('AURA_BACKUP_DIR') or os.path.join(app_data_dir, 'backups')

    bp = Blueprint('backup_api', __name__, url_prefix='/api/backup')

    @bp.route('/create', methods=['POST'])
    def _create():
        denied = _require_admin()
        if denied:
            return denied
        try:
            result = create_backup(product_code, app_data_dir, backup_dir, app_version)
        except BackupError as e:
            return jsonify({'status': 'error', 'message': str(e)}), 400
        except Exception:
            log.exception('Backup creation failed')
            return jsonify({'status': 'error', 'message': 'Backup creation failed.'}), 500
        return jsonify({
            'status': 'ok',
            'filename': os.path.basename(result['path']),
            'manifest': result['manifest'],
        })

    @bp.route('/list', methods=['GET'])
    def _list():
        denied = _require_admin()
        if denied:
            return denied
        if not os.path.isdir(backup_dir):
            return jsonify({'status': 'ok', 'backups': []})
        prefix = f'aura-{product_code}-backup-'
        entries = []
        for name in sorted(os.listdir(backup_dir), reverse=True):
            if name.startswith(prefix) and name.endswith('.zip'):
                full = os.path.join(backup_dir, name)
                entries.append({
                    'filename': name,
                    'size': os.path.getsize(full),
                    'modified_at': os.path.getmtime(full),
                })
        return jsonify({'status': 'ok', 'backups': entries})

    @bp.route('/download/<path:filename>', methods=['GET'])
    def _download(filename):
        denied = _require_admin()
        if denied:
            return denied
        safe_name = os.path.basename(filename)
        if safe_name != filename or not safe_name.startswith(f'aura-{product_code}-backup-'):
            return jsonify({'status': 'error', 'message': 'Invalid backup filename'}), 400
        if not os.path.isfile(os.path.join(backup_dir, safe_name)):
            return jsonify({'status': 'error', 'message': 'Backup not found'}), 404
        return send_from_directory(backup_dir, safe_name, as_attachment=True)

    @bp.route('/restore', methods=['POST'])
    def _restore():
        denied = _require_admin()
        if denied:
            return denied

        upload = request.files.get('file')
        source_path = None
        cleanup_path = None
        try:
            if upload is not None:
                os.makedirs(backup_dir, exist_ok=True)
                # secure_filename() strips path separators and ".." segments
                # -- upload.filename is attacker-controlled (the client sets
                # it in the multipart request) and was previously
                # interpolated into the save path unsanitized, which would
                # let a filename like "../../evil" write outside backup_dir.
                safe_upload_name = secure_filename(upload.filename or '') or 'backup'
                cleanup_path = os.path.join(backup_dir, f'.upload-{os.getpid()}-{safe_upload_name}.zip')
                upload.save(cleanup_path)
                source_path = cleanup_path
            else:
                filename = (request.get_json(silent=True) or {}).get('filename')
                if not filename:
                    return jsonify({'status': 'error', 'message': 'Provide a file upload or a filename'}), 400
                safe_name = os.path.basename(filename)
                if safe_name != filename:
                    return jsonify({'status': 'error', 'message': 'Invalid backup filename'}), 400
                source_path = os.path.join(backup_dir, safe_name)

            result = restore_backup(product_code, app_data_dir, source_path)
        except BackupError as e:
            return jsonify({'status': 'error', 'message': str(e)}), 400
        except Exception:
            log.exception('Restore failed')
            return jsonify({'status': 'error', 'message': 'Restore failed.'}), 500
        finally:
            if cleanup_path and os.path.exists(cleanup_path):
                os.remove(cleanup_path)

        return jsonify({
            'status': 'ok',
            'restored': result['restored'],
            'rollback_dir': result['rollback_dir'],
            'message': 'Restore complete. Restart the application before continuing.',
        })

    return bp


def backup_dir_is_safe(path):
    return is_safe_backup_dir(path)
