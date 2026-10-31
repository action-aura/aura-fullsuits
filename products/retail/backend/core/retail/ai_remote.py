"""Aura Retail -- the till's side of the AI call to Owner.

The till NEVER holds an AI key (spec R2). It sends a device-signed request to
Owner's /ai/shop-analysis; Owner calls the AI provider and meters the call per
licence. This module is the only place that builds that call, and it mirrors
how the crash-report sender and the mail relay wire themselves
(commercial_runtime/crash_reports/sender.py, app.py's _get_mail_relay):

  * the same Owner base URL, TLS and timeout settings from config;
  * the installation id Owner issued, read from licensing.db;
  * the same device key (WindowsDpapiDeviceIdentityProvider over
    <app_data>/licensing) -- never a second key;
  * `max_retries=1`, 60 s timeout (a report is a long generation).

ANDROID is excluded exactly as the mail relay and crash sender are: Python on
Android has no signing key (Kotlin holds it, see android_bridge_identity), so
`available()` is False there and the routes say "not available on this
device". AI on the phone is a separate wave.

Everything the routes need from the outside world is reached through the three
small module-level seams (`_make_client`, `_make_signer`, `_installation_id`)
so tests substitute a fake client and assert what was SENT, without a network.
"""
import logging
from pathlib import Path

import config

log = logging.getLogger(__name__)

#: A report can take a while for the model to write.
AI_TIMEOUT_SECONDS = 60.0

#: Local error codes the routes translate into friendly messages.
NOT_AVAILABLE = 'AI_NOT_AVAILABLE'          # no Owner URL / not activated / Android
NOT_CONFIGURED = 'AI_NOT_CONFIGURED'        # Owner has no AI key yet
BUDGET = 'AI_BUDGET_EXHAUSTED'              # this licence's monthly limit
BUSY = 'AI_UPSTREAM_ERROR'                  # AI service down/overloaded, network
PRIVACY = 'AI_PRIVACY_REFUSED'
LICENCE = 'AI_LICENCE_PROBLEM'              # auth / licence / subscription refusal
BAD_REPLY = 'AI_BAD_REPLY'                  # Owner answered something unusable

#: Owner reason codes -> local codes. Anything not listed (INVALID_SIGNATURE,
#: ACTIVATION_REJECTED, ... every auth refusal) is a licence problem; Owner
#: collapses its licence states into one public code on purpose, so the till
#: cannot and should not say more.
_OWNER_CODE_MAP = {
    'AI_NOT_CONFIGURED': NOT_CONFIGURED,
    'AI_BUDGET_EXHAUSTED': BUDGET,
    'AI_UPSTREAM_ERROR': BUSY,
    'AI_RATE_LIMITED': BUSY,
    'RATE_LIMITED': BUSY,
    'SERVICE_TEMPORARILY_UNAVAILABLE': BUSY,
    'AI_PRIVACY_REFUSED': PRIVACY,
}


class AiRemoteError(Exception):
    def __init__(self, code, detail=''):
        super().__init__(detail or code)
        self.code = code


def _app_data():
    return Path(config.DATABASE_DIR).parent


def _installation_id():
    """The Owner-issued installation id, or None before activation."""
    from commercial_runtime.licensing_contracts.state_repository import LicenseStateRepository
    db_path = _app_data() / 'database' / 'subsystems' / 'licensing.db'
    if not db_path.exists():
        return None
    try:
        state = LicenseStateRepository(db_path).load()
    except Exception:  # noqa: BLE001 -- unreadable licence db == not usable
        log.warning('ai: could not read the licence state')
        return None
    return getattr(state, 'owner_installation_id', None) or None


def _make_signer():
    # Lazy: the Windows provider pulls in the Windows-only `cryptography`
    # path; never import it at module load (the Android import crash app.py's
    # own comment describes).
    from commercial_runtime.licensing_contracts.device_identity import WindowsDpapiDeviceIdentityProvider
    return WindowsDpapiDeviceIdentityProvider(_app_data() / 'licensing')


def _make_client():
    from commercial_runtime.licensing_contracts.client import LicensingClient, LicensingClientConfig
    return LicensingClient(LicensingClientConfig(
        base_url=config.OWNER_LICENSING_BASE_URL, timeout_seconds=AI_TIMEOUT_SECONDS,
        verify_tls=config.OWNER_LICENSING_VERIFY_TLS, max_retries=1))


def available():
    """Owner is reachable IN PRINCIPLE: a URL is configured, this is not
    Android, and the install holds an Owner-issued installation id. No network
    call -- like /ai/status, it answers "could this install try", not "is
    Owner up right now"."""
    if not config.OWNER_LICENSING_BASE_URL:
        return False
    if config.LICENSING_PLATFORM == 'ANDROID':
        return False
    return bool(_installation_id())


def request(kind, lang, snapshot, question=None, history=None):
    """One AI call. Returns Owner's ACCEPTED body; raises AiRemoteError with a
    local code otherwise. Never logs the snapshot, the question or the reply
    (shop figures and free text)."""
    if not available():
        raise AiRemoteError(NOT_AVAILABLE)
    try:
        response = _make_client().ai_shop_analysis(
            installation_id=_installation_id(), signer=_make_signer(),
            app_version=config.APP_VERSION, platform=config.LICENSING_PLATFORM,
            kind=kind, lang=lang, snapshot=snapshot, question=question, history=history)
    except Exception as exc:  # noqa: BLE001 -- transport failure of any kind
        log.warning('ai: request failed (%s)', getattr(exc, 'reason_code', type(exc).__name__))
        raise AiRemoteError(BUSY) from exc
    if not isinstance(response, dict):
        raise AiRemoteError(BAD_REPLY)
    if response.get('result') == 'ACCEPTED':
        return response
    reason = str(response.get('reason_code') or '')
    code = _OWNER_CODE_MAP.get(reason, LICENCE)
    log.warning('ai: Owner refused (%s)', reason)
    raise AiRemoteError(code, reason)


#: Friendly text per local code. The UI shows this verbatim; Arabic first-class
#: because the product ships Arabic-first.
NOT_ENABLED = 'AI_NOT_ENABLED'
MESSAGES = {
    NOT_AVAILABLE: {
        'en': 'Smart analysis is not available on this device.',
        'ar': 'التحليل الذكي غير متاح على هذا الجهاز.',
    },
    NOT_CONFIGURED: {
        'en': 'Smart analysis is not switched on at our server yet.',
        'ar': 'التحليل الذكي لم يُفعَّل على خادمنا بعد.',
    },
    BUDGET: {
        'en': "This month's smart analysis limit has been reached.",
        'ar': 'تم الوصول إلى حدّ التحليل الذكي لهذا الشهر.',
    },
    BUSY: {
        'en': 'The AI service is busy or unreachable. Please try again later.',
        'ar': 'خدمة الذكاء الاصطناعي مشغولة أو غير متاحة. حاول مرة أخرى لاحقاً.',
    },
    PRIVACY: {
        'en': 'Smart analysis was refused for privacy reasons.',
        'ar': 'تم رفض التحليل الذكي لأسباب تتعلق بالخصوصية.',
    },
    LICENCE: {
        'en': 'Smart analysis needs an active licence on this device.',
        'ar': 'يحتاج التحليل الذكي إلى ترخيص فعّال على هذا الجهاز.',
    },
    BAD_REPLY: {
        'en': 'The AI service sent an unusable answer. Please try again.',
        'ar': 'أرسلت خدمة الذكاء الاصطناعي إجابة غير صالحة. حاول مرة أخرى.',
    },
    NOT_ENABLED: {
        'en': 'Smart analysis is turned off. The shop owner can turn it on in Settings.',
        'ar': 'التحليل الذكي متوقف. يمكن لصاحب المتجر تفعيله من الإعدادات.',
    },
}


def message(code, lang):
    entry = MESSAGES.get(code) or MESSAGES[BUSY]
    return entry.get(lang) or entry['en']


#: HTTP status per local code. 5xx/429 only -- never 401/403, which the
#: frontend's fetch helper treats as "your session or permission is gone".
STATUS = {NOT_AVAILABLE: 503, NOT_CONFIGURED: 503, BUDGET: 429, BUSY: 503,
          PRIVACY: 503, LICENCE: 503, BAD_REPLY: 502, NOT_ENABLED: 409}
