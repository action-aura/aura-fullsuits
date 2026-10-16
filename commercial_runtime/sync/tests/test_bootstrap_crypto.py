"""SYNC-BOOT Phase 3: the mailbox encryption. Each guard is tested in BOTH
directions (the right input opens; the wrong one is refused)."""
from __future__ import annotations

import os

import pytest

from commercial_runtime.sync.bootstrap import crypto, errors
from commercial_runtime.sync.bootstrap.errors import BootstrapError

RID = "11111111-1111-1111-1111-111111111111"


@pytest.fixture(autouse=True)
def small_chunks(monkeypatch):
    # Several chunks from a few KB: the chunk logic is size-independent.
    monkeypatch.setattr(crypto, "CHUNK_SIZE", 1024)


def _make(data=None):
    data = data if data is not None else os.urandom(2500)
    keys = crypto.new_requester_keys()
    params, gen = crypto.encrypt_package(data, keys.public_b64, RID)
    return data, keys, params, list(gen)


def test_roundtrip_and_ciphertext_is_not_plaintext():
    data, keys, params, chunks = _make(b"SECRET-SHOP-ROW " * 200)
    assert params.chunk_count == len(chunks) == 4
    assert all(b"SECRET-SHOP-ROW" not in c for c in chunks)
    assert crypto.decrypt_package(chunks, keys.private_b64, params.as_dict(), RID) == data


def test_wrong_requester_key_is_refused():
    _, _, params, chunks = _make()
    other = crypto.new_requester_keys()
    with pytest.raises(BootstrapError) as e:
        crypto.decrypt_package(chunks, other.private_b64, params.as_dict(), RID)
    assert e.value.reason_code == errors.CHECKSUM_MISMATCH


def test_tampered_chunk_is_refused():
    _, keys, params, chunks = _make()
    bad = bytearray(chunks[1])
    bad[5] ^= 1
    chunks[1] = bytes(bad)
    with pytest.raises(BootstrapError):
        crypto.decrypt_package(chunks, keys.private_b64, params.as_dict(), RID)


def test_swapped_chunks_are_refused_because_the_index_is_authenticated():
    _, keys, params, chunks = _make()
    chunks[0], chunks[1] = chunks[1], chunks[0]
    with pytest.raises(BootstrapError):
        crypto.decrypt_package(chunks, keys.private_b64, params.as_dict(), RID)


def test_a_chunk_from_another_request_is_refused():
    _, keys, params, chunks = _make()
    with pytest.raises(BootstrapError):
        crypto.decrypt_package(chunks, keys.private_b64, params.as_dict(), "22222222-2222-2222-2222-222222222222")


def test_truncation_is_refused_because_the_total_is_authenticated():
    _, keys, params, chunks = _make()
    # An attacker who can edit the mailbox drops the last chunk AND the declared count.
    short = dict(params.as_dict(), chunk_count=params.chunk_count - 1, plaintext_size=None)
    dec = crypto.ChunkDecryptor(keys.private_b64, short, RID)
    with pytest.raises(BootstrapError):
        dec.decrypt_chunk(0, chunks[0])            # authenticated under total=3, not 2


def test_chunk_count_mismatch_is_refused():
    _, keys, params, chunks = _make()
    with pytest.raises(BootstrapError):
        crypto.decrypt_package(chunks[:-1], keys.private_b64, params.as_dict(), RID)


def test_empty_package_still_has_one_authenticated_chunk():
    _, keys, params, chunks = _make(b"")
    assert len(chunks) == 1
    assert crypto.decrypt_package(chunks, keys.private_b64, params.as_dict(), RID) == b""


def test_keys_are_single_use_each_encryption_is_different():
    data = b"x" * 100
    keys = crypto.new_requester_keys()
    p1, g1 = crypto.encrypt_package(data, keys.public_b64, RID)
    p2, g2 = crypto.encrypt_package(data, keys.public_b64, RID)
    assert p1.ephemeral_pub_b64 != p2.ephemeral_pub_b64 and list(g1) != list(g2)
