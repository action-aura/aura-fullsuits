"""Where the Owner ticket lives between the screens of the account sign-in.

The sign-in wizard is several requests (sign in, change password, accept the
contract, pick a branch, activate). The Owner ticket that ties them together is
a bearer credential for the length of its life (minutes), so it is kept here, in
THIS process's memory, and the page gets only an opaque handle (`flow_id`). It
never reaches page JavaScript, the DOM, local storage, a cookie, a log or a
database -- a person looking over the shoulder of the developer tools sees a
random string that means nothing to Owner.

In memory only, on purpose: a restart of the app simply asks the person to sign
in again, which is what they would expect. Bounded (a few entries, a short
life) so a script hammering the route cannot grow it.

The store is also where the local ORDER of the steps is enforced -- a forced
password change must happen before a contract can be accepted, and the contract
before activation -- so a broken or tampered page cannot skip a step. Owner
enforces the same rules (CONTRACT_ACCEPTANCE_REQUIRED, ACTIVATION_GRANT_INVALID);
this is the first line, not the only one.
"""
from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from .account_flow import SignInResult

DEFAULT_TTL_SECONDS = 15 * 60
MAX_ENTRIES = 8


@dataclass
class AccountSession:
    ticket: str = field(repr=False)
    result: SignInResult
    expires_at_monotonic: float
    password_change_pending: bool
    contract_accepted: bool

    @property
    def contract_pending(self) -> bool:
        return bool(self.result.contract.required) and not self.contract_accepted

    def licence_ids(self) -> set:
        return {c.license_public_id for c in self.result.licences}


class AccountSessionStore:
    def __init__(self, ttl_seconds: float = DEFAULT_TTL_SECONDS, max_entries: int = MAX_ENTRIES,
                 clock: Callable[[], float] = time.monotonic):
        self._ttl = ttl_seconds
        self._max = max_entries
        self._clock = clock
        self._lock = threading.Lock()
        self._entries: dict = {}

    def _sweep(self) -> None:
        now = self._clock()
        for handle in [h for h, e in self._entries.items() if e.expires_at_monotonic <= now]:
            self._entries.pop(handle, None)

    def create(self, result: SignInResult) -> str:
        with self._lock:
            self._sweep()
            while len(self._entries) >= self._max:
                # Drop the oldest: a person who started over abandoned it.
                oldest = min(self._entries, key=lambda h: self._entries[h].expires_at_monotonic)
                self._entries.pop(oldest, None)
            handle = secrets.token_urlsafe(24)
            self._entries[handle] = AccountSession(
                ticket=result.ticket,
                result=result,
                expires_at_monotonic=self._clock() + self._ttl,
                password_change_pending=result.password_change_required,
                contract_accepted=False,
            )
            return handle

    def get(self, handle) -> Optional[AccountSession]:
        if not isinstance(handle, str) or not handle:
            return None
        with self._lock:
            self._sweep()
            return self._entries.get(handle)

    def replace_ticket(self, handle: str, ticket: str) -> None:
        with self._lock:
            entry = self._entries.get(handle)
            if entry is not None:
                entry.ticket = ticket

    def mark_password_changed(self, handle: str) -> None:
        with self._lock:
            entry = self._entries.get(handle)
            if entry is not None:
                entry.password_change_pending = False

    def mark_contract_accepted(self, handle: str) -> None:
        with self._lock:
            entry = self._entries.get(handle)
            if entry is not None:
                entry.contract_accepted = True

    def discard(self, handle) -> None:
        with self._lock:
            entry = self._entries.pop(handle, None) if isinstance(handle, str) else None
            if entry is not None:
                entry.ticket = None
