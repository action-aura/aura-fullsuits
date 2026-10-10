"""System -> Backup must not hand pg_dump a newer server (live-deploy audit
2026-10-10, finding B1: the image had pg_dump 17 against Neon's PostgreSQL 18).

Assertions count the real dump invocation (ENGINEERING 1): "an error was
raised" cannot tell the guard ran from pg_dump itself refusing, so the
too-old case asserts pg_dump's DUMP command was never executed, and the
matching case asserts it was (the allow half).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app.system import backup


class _FakeRun:
    def __init__(self, client_version_text, tmp_path):
        self.client_version_text = client_version_text
        self.dump_calls = 0
        self.version_calls = 0

    def __call__(self, cmd, **kwargs):
        if "--version" in cmd:
            self.version_calls += 1
            return subprocess.CompletedProcess(cmd, 0, stdout=self.client_version_text, stderr="")
        self.dump_calls += 1
        Path(cmd[cmd.index("--file") + 1]).write_bytes(b"PGDMP-fake")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")


def _wire(monkeypatch, tmp_path, client_text, server_major):
    fake = _FakeRun(client_text, tmp_path)
    monkeypatch.setattr(backup, "_pg_bin", lambda tool: "/fake/pg_dump")
    monkeypatch.setattr(backup, "_server_major", lambda: server_major)
    monkeypatch.setattr(backup.subprocess, "run", fake)
    return fake


def test_older_client_than_server_fails_clearly_and_never_runs_the_dump(app, tmp_path, monkeypatch):
    fake = _wire(monkeypatch, tmp_path, "pg_dump (PostgreSQL) 17.11 (Debian 17.11-1.pgdg13+1)\n", 18)
    with app.app_context():
        with pytest.raises(backup.BackupError) as err:
            backup.create_backup(str(tmp_path), None)
    msg = str(err.value)
    assert "17" in msg and "18" in msg and "postgresql-client-18" in msg
    assert fake.version_calls == 1, "the guard must actually have run"
    assert fake.dump_calls == 0, "pg_dump must never be invoked for the dump"


def test_matching_client_proceeds_to_the_dump(app, tmp_path, monkeypatch):
    fake = _wire(monkeypatch, tmp_path, "pg_dump (PostgreSQL) 18.1\n", 18)
    with app.app_context():
        status = backup.create_backup(str(tmp_path), None).status
    assert status == "SUCCESS"
    assert (fake.version_calls, fake.dump_calls) == (1, 1)


def test_newer_client_than_server_is_allowed(app, tmp_path, monkeypatch):
    fake = _wire(monkeypatch, tmp_path, "pg_dump (PostgreSQL) 18.1\n", 17)
    with app.app_context():
        status = backup.create_backup(str(tmp_path), None).status
    assert status == "SUCCESS"
    assert fake.dump_calls == 1


def test_client_major_is_read_from_the_version_banner():
    assert backup._major("pg_dump (PostgreSQL) 17.11 (Debian 17.11-1)") == 17


def test_dockerfile_staging_installs_pgdg_client_18():
    """Static guard: a revert to Debian's postgresql-client (major 17) silently
    brings the broken backup button back, and nothing else would notice."""
    text = (Path(__file__).resolve().parent.parent / "Dockerfile.staging").read_text()
    assert "postgresql-client-18" in text
    assert "apt.postgresql.org" in text and "signed-by=" in text and "-pgdg" in text
    assert "--no-install-recommends postgresql-client curl" not in text
