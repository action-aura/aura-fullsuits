"""Which OWNER_LICENSE_PEPPER issued a given licence key? Answer by evidence.

Owner never stores a licence key, only HMAC(key, pepper). So the only way to
know which pepper a database was run with is to take a key that was issued
against it and try candidates. Run from owner/ with the Owner venv:

    python ../scripts/ops/verify_pepper.py <DB_NAME> <LICENSE_KEY> [pepper ...]

Candidates default to the ones a local run can plausibly have used. Prints the
matching pepper or NONE. Read-only.
"""
import os
import sys

sys.path.insert(0, os.getcwd())

import psycopg  # noqa: E402

from app.security.license_keys import hash_license_secret  # noqa: E402

DEFAULT_CANDIDATES = [
    "dev-only-insecure-pepper-do-not-use-in-production",  # DevelopmentConfig default when unset
    "demo-local-pepper-not-for-production",               # the demo Owner's documented value
    "uishots-local-only-pepper",                          # scripts/ui-sweep/owner_bootstrap.sh
    "test-license-pepper",                                # TestConfig
]


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    db, key = sys.argv[1], sys.argv[2].strip().upper()
    candidates = sys.argv[3:] or DEFAULT_CANDIDATES
    dsn = f"postgresql://aura_owner:aura_owner_dev@localhost:5432/{db}"
    with psycopg.connect(dsn, connect_timeout=5) as conn:
        stored = {row[0] for row in conn.execute("SELECT key_secret_hmac FROM owner_licenses")}
    for pepper in candidates:
        if hash_license_secret(key, pepper) in stored:
            print(f"MATCH: {pepper}")
            return 0
    print("NONE of the candidates produced a stored HMAC for that key")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
