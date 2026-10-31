"""Cross-stack check: the website's TypeScript signer must agree with Owner's Python signer, byte for byte.

    python -I tools/check_signature_vectors.py            # verify tests/unit/fixtures/signature-vectors.json
    python -I tools/check_signature_vectors.py --write    # regenerate that file from Owner's helper

The Owner side is imported READ-ONLY: its signature.py is read out of git (branch feat/step2-accounts-ca1 by
default, or any ref/--owner-file given) and executed from memory with a stub for `app.config`. Nothing is checked
out and no Owner file is touched. The stub's one method mirrors BaseConfig._looks_like_a_real_secret closely
enough for the test secrets (long, not a placeholder); the HMAC / canonical-string code that runs is Owner's own.

What "verify" proves, for every vector in the file:
  1. Owner's sign() reproduces the stored signature   -> the file is Owner-correct.
  2. Owner's verify() ACCEPTS the stored header        -> Owner would accept what the website sends.
  3. Owner's verify() REJECTS tampered copies (body, path, method, timestamp skew, wrong kid)
     -> the check is not vacuous: it can fail.
website/tests/unit/owner-account.test.ts then asserts the TypeScript signer yields the same signatures from the same
file. Together: TS == file == Owner.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import pathlib
import subprocess
import sys
import types

HERE = pathlib.Path(__file__).resolve().parent
FIXTURE = HERE.parent / "tests" / "unit" / "fixtures" / "signature-vectors.json"
DEFAULT_REF = "feat/step2-accounts-ca1"
OWNER_PATH = "owner/app/customer_accounts/signature.py"

# The test secrets are fake, committed on purpose, and appear in docs/owner/customer-accounts/signature-v1.md.
VECTORS = [
    {
        "name": "documented known-answer vector",
        "secret": "aura-kat-secret-0123456789abcdefghijklmnopqrstuv",  # pragma: allowlist secret
        "kid": "k1", "method": "POST", "path": "/api/website/v1/account/sign-in",
        "ts": "1790000000", "nonce": "kat-nonce-0123456789ab",
        "body": '{"email":"owner@haddad.example","password":"Tr0ub4dor-and-3-horses","client_ip":"203.0.113.5","lang":"en"}',
    },
    {
        "name": "arabic text and an IPv6 client address",
        "secret": "aura-kat-secret-0123456789abcdefghijklmnopqrstuv",  # pragma: allowlist secret
        "kid": "k1", "method": "POST", "path": "/api/website/v1/account/sign-in",
        "ts": "1790000001", "nonce": "kat-nonce-arabic-0001",
        "body": json.dumps({"email": "عميل@مثال.example", "password": "كلمة-سر-طويلة-123", "client_ip": "2001:db8::1", "lang": "ar"},
                           ensure_ascii=False, separators=(",", ":")),
    },
    {
        "name": "empty object on a different route",
        "secret": "aura-kat-secret-0123456789abcdefghijklmnopqrstuv",  # pragma: allowlist secret
        "kid": "k1", "method": "POST", "path": "/api/website/v1/account/sign-out",
        "ts": "1790000002", "nonce": "kat-nonce-empty-00001", "body": "{}",
    },
    {
        "name": "escapes, quotes and a newline inside the JSON text",
        "secret": "aura-kat-secret-0123456789abcdefghijklmnopqrstuv",  # pragma: allowlist secret
        "kid": "k1", "method": "POST", "path": "/api/website/v1/account/password/change",
        "ts": "1790000003", "nonce": "kat-nonce-escape-0001",
        "body": json.dumps({"current_password": 'a"b\\c\nd', "new_password": "x y\tz", "client_ip": "198.51.100.7"},
                           separators=(",", ":")),
    },
    {
        "name": "second key slot, non-ASCII secret, lower-case method input",
        "secret": "سر-سري-طويل-جدا-0123456789abcdefghij",  # pragma: allowlist secret
        "kid": "k2", "method": "post", "path": "/api/website/v1/account/summary",
        "ts": "1790000099", "nonce": "kat_nonce-second-slot-9",
        "body": '{"session_token":"abcdefghijklmnopqrstuvwxyz012345","client_ip":"203.0.113.9","lang":"en"}',
    },
]


def load_owner_signature(owner_file: str | None, ref: str):
    if owner_file:
        source = pathlib.Path(owner_file).read_text(encoding="utf-8")
    else:
        top = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=HERE, check=True, capture_output=True, text=True).stdout.strip()
        source = subprocess.run(["git", "show", f"{ref}:{OWNER_PATH}"], cwd=top, check=True, capture_output=True).stdout.decode("utf-8")
    app = types.ModuleType("app")
    app.__path__ = []  # type: ignore[attr-defined]
    cfg = types.ModuleType("app.config")

    class BaseConfig:  # stub: only what signature.py touches
        @staticmethod
        def _looks_like_a_real_secret(value: str) -> bool:
            return bool(value) and len(value) >= 32 and "change" not in value.lower()

    cfg.BaseConfig = BaseConfig  # type: ignore[attr-defined]
    sys.modules["app"], sys.modules["app.config"] = app, cfg
    module = types.ModuleType("owner_signature")
    module.__file__ = OWNER_PATH
    sys.modules["owner_signature"] = module  # dataclasses looks its module up here
    exec(compile(source, OWNER_PATH, "exec"), module.__dict__)
    return module


def compute(sig_mod, v: dict) -> dict:
    body = v["body"].encode("utf-8")
    sig = sig_mod.sign(v["secret"], v["method"], v["path"], v["ts"], v["nonce"], body)
    return {
        **v,
        "body_sha256": hashlib.sha256(body).hexdigest(),
        "sig": sig,
        "header": f"v1,kid={v['kid']},ts={v['ts']},nonce={v['nonce']},sig={sig}",
    }


def owner_accepts(sig_mod, v: dict, header: str, *, method=None, path=None, body=None, now=None) -> bool:
    keys = {"WEBSITE_ACCOUNT_KEY_ID": v["kid"], "WEBSITE_ACCOUNT_SECRET": v["secret"]}
    try:
        sig_mod.verify(keys, header, method or v["method"], path or v["path"],
                       (body if body is not None else v["body"]).encode("utf-8"), now=now if now is not None else int(v["ts"]))
    except sig_mod.SignatureError:
        return False
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--ref", default=DEFAULT_REF)
    ap.add_argument("--owner-file")
    args = ap.parse_args()
    sig_mod = load_owner_signature(args.owner_file, args.ref)
    computed = [compute(sig_mod, v) for v in VECTORS]

    if args.write:
        FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE.write_text(json.dumps(computed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
        print(f"wrote {len(computed)} vectors to {FIXTURE}")
        return 0

    stored = json.loads(FIXTURE.read_text(encoding="utf-8"))
    problems: list[str] = []
    if stored != computed:
        problems.append("fixture differs from what Owner's signer produces (run with --write, then review the diff)")
    for v in stored:
        n = v["name"]
        if not owner_accepts(sig_mod, v, v["header"]):
            problems.append(f"{n}: Owner verify() REJECTED the stored header")
        header_other_sig = v["header"][:-1] + ("0" if v["header"][-1] != "0" else "1")
        tampered = {
            "body": dict(body=v["body"] + " "),
            "path": dict(path=v["path"] + "x"),
            "method": dict(method="GET" if v["method"].upper() != "GET" else "POST"),
            "late (61 s skew)": dict(now=int(v["ts"]) + 61),
            "flipped sig bit": dict(header=header_other_sig),
        }
        for what, kw in tampered.items():
            header = kw.pop("header", v["header"])
            if owner_accepts(sig_mod, v, header, **kw):
                problems.append(f"{n}: Owner verify() ACCEPTED a request with a tampered {what}")
        if owner_accepts(sig_mod, {**v, "kid": "other"}, v["header"]):
            problems.append(f"{n}: Owner verify() accepted an unknown kid")
    if problems:
        print("FAIL")
        print("\n".join(" - " + p for p in problems))
        return 1
    print(f"OK: {len(stored)} vectors match Owner's signature.py ({args.owner_file or args.ref}); tampering is rejected for each")
    return 0


if __name__ == "__main__":
    sys.exit(main())
