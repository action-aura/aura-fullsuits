#!/usr/bin/env python3
"""
check_mail_dns.py -- "is this domain's email set up correctly?" in one command.

Usage:
    python check_mail_dns.py [domain]      (default domain: actionaura.me)

What it checks, and why each one matters:
    1. MX      -- can mail even be DELIVERED to this domain at all.
    2. SPF     -- can OTHER mail servers tell who is allowed to send as you.
    3. DKIM    -- can other mail servers tell your mail wasn't tampered with.
    4. DMARC   -- what should happen when SPF/DKIM fail, and do you get told.

Only MX and SPF affect the exit code (0 = both pass, 1 = otherwise), because
those two are the ones that break actual mail DELIVERY. DKIM and DMARC affect
trust and spam placement, not delivery, so a missing DKIM/DMARC record never
fails the exit code -- but it is still reported, because "delivers" and
"lands in the inbox instead of spam" are different bars to clear.

Every line of output starts with one of four tokens so a non-expert can scan
it without understanding DNS: PASS, FAIL, WARN, UNKNOWN.

UNKNOWN is a first-class outcome, not a bug. Two things can make a DNS
answer impossible to trust:
  - the resolver gave a clean, confident "this doesn't exist" (NXDOMAIN /
    empty NOERROR) -> that's a real PASS/FAIL, we report it as such.
  - the resolver timed out, choked on a large/truncated response, or said
    something we don't recognise -> that's UNKNOWN, and this tool reports it
    as UNKNOWN rather than guessing FAIL. Reporting "missing" for something
    we simply failed to check would send an owner to add a DUPLICATE record
    (see the SPF section below for why two is worse than none) -- that
    failure mode is worse than the tool saying nothing at all.

This is not a theoretical concern in this codebase's own network: apex TXT
lookups for domains with many TXT records (large providers publish several
verification records at once) get UDP-truncated by the local router here,
and TCP:53 fallback is unavailable on this network -- confirmed with
`nslookup -debug` and `nslookup -vc` while writing this tool. A query that
degrades this way is reported UNKNOWN, never FAIL.

Dependencies: Python 3 standard library only, plus the `nslookup` binary,
which must be on PATH. No dnspython requirement -- if dnspython happens to be
importable this script does not use it, on purpose, so it behaves exactly
the same on a bare `python3` install as it does in a repo venv.

Only the DEFAULT system resolver is queried. Never hardcode a resolver IP:
on this network public resolvers (8.8.8.8 etc.) are firewalled off and any
query aimed at one will simply hang until it times out.
"""

import re
import subprocess
import sys

DEFAULT_DOMAIN = "actionaura.me"

# Common DKIM selectors to try. There is no way to enumerate selectors from
# outside DNS -- this is a guess list, not an exhaustive one, and a miss is
# reported as UNKNOWN rather than FAIL. See check_dkim()'s UNKNOWN path.
#
# `privateemail` is FIRST because it is this project's own provider, and its
# omission was a real defect rather than a gap: the tool was written against
# actionaura.me, reported UNKNOWN for it, and that was taken as "DKIM is not
# set up yet" -- when Namecheap had in fact published a 2048-bit key all
# along, under a selector the list did not carry. A guess list that misses
# the one domain the tool exists to check is worse than no list, because
# UNKNOWN reads as absence to anyone who is not reading carefully.
DKIM_SELECTORS = [
    "privateemail", "default", "dkim", "mail", "selector1", "selector2",
    "google", "k1", "s1", "s2", "pm", "mandrill", "zoho",
    # Other provider-specific names seen in the wild, cheap to try:
    "namecheap", "fm1", "fm2", "fm3", "protonmail", "mailjet", "sendgrid",
]

# Per-nslookup-call tuning. Kept modest because DKIM alone issues up to
# len(DKIM_SELECTORS) queries -- a long per-query timeout multiplies badly.
NSLOOKUP_QUERY_TIMEOUT_SECONDS = 3
NSLOOKUP_RETRIES = 1
# Hard ceiling on the subprocess itself, as a safety net in case nslookup's
# own -timeout/-retry flags are ignored by some platform's build of it.
SUBPROCESS_TIMEOUT_SECONDS = 12

PASS, FAIL, WARN, UNKNOWN = "PASS", "FAIL", "WARN", "UNKNOWN"

# nslookup always prefixes a negative answer with "*** <server> can't find
# <name>: <reason>". The REASON is what tells a confirmed absence (safe to
# call FAIL) apart from a resolver-level failure (must be UNKNOWN). These
# lists are deliberately conservative: anything not recognised falls through
# to UNKNOWN rather than being assumed to be either.
CONFIRMED_ABSENT_MARKERS = (
    "non-existent domain", "no records", "no such name",
    "record does not exist",
)
INDETERMINATE_MARKERS = (
    "timed out", "timed-out", "unspecified error", "server failed",
    "refused", "no response from server", "connection timed out",
    "format error",
)

# Matches a TXT-answer header line, e.g. "actionaura.me\ttext =" (nslookup
# repeats one of these per TXT answer, so counting/splitting on them is how
# multiple TXT records for the same name -- the SPF double-record case -- get
# told apart from one record's value being wrapped across quoted chunks).
_TXT_HEADER_RE = re.compile(r"^\S.*?\btext\s*=\s*$", re.IGNORECASE | re.MULTILINE)
_QUOTED_CHUNK_RE = re.compile(r'"((?:[^"\\]|\\.)*)"')
_MX_RE = re.compile(
    r"MX preference\s*=\s*(\d+),\s*mail exchanger\s*=\s*(\S+)", re.IGNORECASE
)
_NULL_MX_HOSTS = (".", "(root)")


def run_nslookup(name: str, record_type: str):
    """Run `nslookup -type=<record_type> <name>` against the DEFAULT resolver.

    Returns (combined_output, ran_ok). ran_ok is False only when the
    subprocess itself could not be started or finished in time -- DNS-level
    failures (NXDOMAIN, timeout-per-nslookup's-own-flags, truncation) are
    NOT subprocess failures; they come back as ran_ok=True with the failure
    text inside combined_output, for the caller to classify.
    """
    cmd = [
        "nslookup",
        f"-timeout={NSLOOKUP_QUERY_TIMEOUT_SECONDS}",
        f"-retry={NSLOOKUP_RETRIES}",
        f"-type={record_type}",
        name,
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=SUBPROCESS_TIMEOUT_SECONDS,
        )
        return (proc.stdout or "") + "\n" + (proc.stderr or ""), True
    except subprocess.TimeoutExpired:
        return f"[nslookup hung past {SUBPROCESS_TIMEOUT_SECONDS}s and was killed]", False
    except FileNotFoundError:
        return "[nslookup is not installed / not on PATH]", False
    except OSError as exc:
        return f"[could not run nslookup: {exc}]", False


def classify_no_match(raw_output: str) -> str:
    """When a query returned no parseable records, decide whether DNS gave a
    confident negative answer ("ABSENT") or the result is indeterminate
    ("UNKNOWN"). Defaults to UNKNOWN for anything not explicitly recognised
    as a confirmed absence -- see the module docstring for why that default
    matters more than it looks like it should.
    """
    text = raw_output.lower()
    if any(marker in text for marker in INDETERMINATE_MARKERS):
        return UNKNOWN
    if any(marker in text for marker in CONFIRMED_ABSENT_MARKERS):
        return "ABSENT"
    return UNKNOWN


def extract_txt_records(raw_output: str):
    """Return a list of fully-joined TXT record values from one TXT query's
    nslookup output. Handles all of:
      - a single unquoted-chunk value (the common case)
      - a value split into multiple quoted chunks on one line (long DKIM
        keys ARE split at 255 bytes on the wire; nslookup shows them as
        several adjacent quoted strings that must be joined, not concatenated
        with the quotes still attached)
      - multiple separate TXT answers for the same name (nslookup repeats a
        "<name> text =" header per answer -- this is what lets SPF's
        "more than one record" WARN case be told apart from one record that
        merely wrapped across lines)
    """
    headers = list(_TXT_HEADER_RE.finditer(raw_output))
    if not headers:
        # Some nslookup builds/locales might print "text = "value"" inline
        # without matching the header shape above. Best-effort fallback:
        # if the word appears at all, grab whatever quoted chunks exist.
        if re.search(r"\btext\s*=", raw_output, re.IGNORECASE):
            chunks = _QUOTED_CHUNK_RE.findall(raw_output)
            if chunks:
                return ["".join(chunks)]
        return []

    records = []
    for i, header in enumerate(headers):
        start = header.end()
        end = headers[i + 1].start() if i + 1 < len(headers) else len(raw_output)
        block = raw_output[start:end]
        chunks = _QUOTED_CHUNK_RE.findall(block)
        if chunks:
            records.append("".join(chunks))
    return records


def check_mx(domain: str):
    """Returns (status, [report lines])."""
    raw, ran_ok = run_nslookup(domain, "MX")
    if not ran_ok:
        return UNKNOWN, [f"UNKNOWN  MX: {raw.strip()}"]

    matches = _MX_RE.findall(raw)
    if matches:
        real_hosts = [(int(pref), host) for pref, host in matches if host not in _NULL_MX_HOSTS]
        if real_hosts:
            lines = [f"PASS  MX: {len(real_hosts)} record(s) found"]
            for pref, host in sorted(real_hosts):
                lines.append(f"         priority {pref:<3} {host}")
            return PASS, lines
        # Every answer was a null MX (RFC 7505) -- the domain explicitly
        # declared it does not accept mail. That's a definitive, deliberate
        # FAIL, not an absence of configuration.
        return FAIL, [
            "FAIL  MX: domain publishes an explicit null MX (RFC 7505) -- "
            "it has declared it does NOT accept email"
        ]

    absence = classify_no_match(raw)
    if absence == "ABSENT":
        return FAIL, ["FAIL  MX: no MX records found -- this domain cannot receive mail"]
    return UNKNOWN, [
        "UNKNOWN  MX: could not get a clear answer from DNS "
        "(timeout or resolver error) -- try again"
    ]


def check_spf(domain: str):
    raw, ran_ok = run_nslookup(domain, "TXT")
    if not ran_ok:
        return UNKNOWN, [f"UNKNOWN  SPF: {raw.strip()}"]

    records = extract_txt_records(raw)
    spf_records = [r for r in records if r.strip().lower().startswith("v=spf1")]

    if len(spf_records) == 1:
        return PASS, ["PASS  SPF: found", f"        {spf_records[0]}"]

    if len(spf_records) > 1:
        lines = [
            f"WARN  SPF: {len(spf_records)} SPF records found at the domain root "
            "-- this is a hard permerror (RFC 7208) and is WORSE than having none",
        ]
        for r in spf_records:
            lines.append(f"        {r}")
        lines.append(
            "        Fix: delete all but one v=spf1 record. Merge every sending "
            "service into ONE record with multiple 'include:' mechanisms."
        )
        return WARN, lines

    if records:
        # The query succeeded and returned real TXT data -- just none of it
        # was SPF. That is a confirmed absence, not an unknown.
        return FAIL, [
            f"FAIL  SPF: no v=spf1 record found at the domain root "
            f"({len(records)} other TXT record(s) present, none of them SPF)"
        ]

    absence = classify_no_match(raw)
    if absence == "ABSENT":
        return FAIL, ["FAIL  SPF: no v=spf1 record found at the domain root"]
    return UNKNOWN, [
        "UNKNOWN  SPF: could not get a clear answer from DNS "
        "(timeout or resolver error) -- try again"
    ]


def check_dkim(domain: str):
    any_indeterminate = False
    for selector in DKIM_SELECTORS:
        fqdn = f"{selector}._domainkey.{domain}"
        raw, ran_ok = run_nslookup(fqdn, "TXT")
        if not ran_ok:
            any_indeterminate = True
            continue
        records = [r for r in extract_txt_records(raw) if r.strip()]
        if records:
            value = records[0]
            preview = value[:40] + ("..." if len(value) > 40 else "")
            return PASS, [
                f"PASS  DKIM: selector '{selector}' answered",
                f"        {preview}",
            ]
        if classify_no_match(raw) == UNKNOWN:
            any_indeterminate = True

    lines = [
        f"UNKNOWN  DKIM: none of the {len(DKIM_SELECTORS)} common selectors "
        f"tried answered ({', '.join(DKIM_SELECTORS)})",
        "         This does NOT prove DKIM is absent -- only that none of the "
        "selectors this tool guesses at were in use.",
        "         The real selector, if the mail provider uses one, may still "
        "be configured under a different name only they (or their setup docs) know.",
    ]
    if any_indeterminate:
        lines.append(
            "         Some of those lookups also failed to get a clear answer "
            "from DNS (timeout/resolver error), which makes this even less conclusive."
        )
    return UNKNOWN, lines


def check_dmarc(domain: str):
    fqdn = f"_dmarc.{domain}"
    raw, ran_ok = run_nslookup(fqdn, "TXT")
    if not ran_ok:
        return UNKNOWN, [f"UNKNOWN  DMARC: {raw.strip()}"]

    records = extract_txt_records(raw)
    dmarc_records = [r for r in records if r.strip().lower().startswith("v=dmarc1")]

    if dmarc_records:
        value = dmarc_records[0]
        policy_match = re.search(r"\bp\s*=\s*([a-zA-Z]+)", value, re.IGNORECASE)
        policy = policy_match.group(1).lower() if policy_match else None
        rua_present = bool(re.search(r"\brua\s*=", value, re.IGNORECASE))

        if policy == "none":
            status = PASS
            lines = [
                "PASS  DMARC: found, policy p=none (monitor-only -- this is "
                "the correct STARTING state)"
            ]
        elif policy in ("quarantine", "reject"):
            status = PASS
            lines = [f"PASS  DMARC: found, policy p={policy} (enforcing)"]
        elif policy:
            status = WARN
            lines = [f"WARN  DMARC: found, but 'p={policy}' is not a recognised policy value"]
        else:
            status = WARN
            lines = ["WARN  DMARC: found, but no p= policy tag could be parsed out of it"]

        lines.append(f"        {value}")
        if rua_present:
            lines.append("        rua= aggregate-report address is present -- reports will arrive")
        else:
            lines.append(
                "        WARN: no rua= address -- no aggregate reports will "
                "arrive, so tightening the policy later can't be done safely"
            )
        return status, lines

    if records:
        return FAIL, ["FAIL  DMARC: _dmarc TXT record(s) exist but none start with v=DMARC1"]

    absence = classify_no_match(raw)
    if absence == "ABSENT":
        return FAIL, ["FAIL  DMARC: no _dmarc record found"]
    return UNKNOWN, [
        "UNKNOWN  DMARC: could not get a clear answer from DNS "
        "(timeout or resolver error) -- try again"
    ]


def build_summary(domain: str, results: dict) -> list:
    """Plain-English, non-expert summary of what's missing and what to do."""
    lines = ["", f"SUMMARY for {domain}", "-" * (len(domain) + 12)]

    mx_status = results["MX"][0]
    spf_status = results["SPF"][0]
    dkim_status = results["DKIM"][0]
    dmarc_status = results["DMARC"][0]

    if mx_status == PASS and spf_status == PASS:
        lines.append("Mail delivery basics look OK: mail can reach this domain (MX),")
        lines.append("and other servers can tell who's allowed to send as it (SPF).")
    else:
        lines.append("Mail delivery is BROKEN or unverified for this domain right now.")

    if mx_status != PASS:
        lines.append("")
        lines.append("- No usable mail server (MX) is configured. Nobody can email this")
        lines.append("  domain at all until MX records point at a real mail provider.")

    if spf_status == FAIL:
        lines.append("")
        lines.append("- No SPF record. Other mail servers can't verify who's allowed to")
        lines.append("  send email as this domain, so messages are more likely to be")
        lines.append("  flagged as spam or rejected outright. Ask your mail provider for")
        lines.append("  the exact SPF line they want (it usually starts with")
        lines.append("  'v=spf1 include:...') and publish it as a TXT record at the")
        lines.append("  domain root.")
    elif spf_status == WARN:
        lines.append("")
        lines.append("- Two (or more) SPF records exist. This is WORSE than having none:")
        lines.append("  it causes every message to fail SPF outright. Combine them into")
        lines.append("  a single SPF record with one 'include:' per sending service.")

    if dkim_status != PASS:
        lines.append("")
        lines.append("- DKIM could not be confirmed (none of the common selector names")
        lines.append("  this tool tries answered). This does NOT mean it's missing --")
        lines.append("  ask your mail provider what selector they use and check it by")
        lines.append("  hand if you want certainty. DKIM proves your mail wasn't altered")
        lines.append("  in transit; it affects trust and spam placement, not delivery.")

    if dmarc_status == FAIL:
        lines.append("")
        lines.append("- No DMARC record. Without one, other mail servers have no policy")
        lines.append("  to follow when a message fails SPF or DKIM, and you get no")
        lines.append("  visibility into anyone spoofing your domain. Start safe: publish")
        lines.append("  '_dmarc.<domain> TXT \"v=DMARC1; p=none; rua=mailto:you@yourdomain\"'")
        lines.append("  then watch the reports before tightening to quarantine/reject.")
    # (rua= presence/absence guidance is already embedded directly in the
    # DMARC block above, per-line -- nothing further to add here.)

    if mx_status == PASS and spf_status == PASS and dkim_status == PASS and dmarc_status == PASS:
        lines.append("")
        lines.append("Everything checked here looks correctly configured.")

    return lines


def main(argv):
    domain = argv[1] if len(argv) > 1 else DEFAULT_DOMAIN
    domain = domain.strip().rstrip(".")
    if not domain:
        print("UNKNOWN  usage: python check_mail_dns.py [domain]  (default: actionaura.me)")
        return 1

    print(f"Checking mail DNS setup for: {domain}")
    print(f"(using the system's default DNS resolver -- not a public one)")
    print()

    results = {}
    for label, check_fn in (("MX", check_mx), ("SPF", check_spf), ("DKIM", check_dkim), ("DMARC", check_dmarc)):
        try:
            status, lines = check_fn(domain)
        except Exception as exc:  # noqa: BLE001 -- last-resort guard, must never crash
            status, lines = UNKNOWN, [f"UNKNOWN  {label}: internal error while checking -- {exc!r}"]
        results[label] = (status, lines)
        for line in lines:
            print(line)
        print()

    for line in build_summary(domain, results):
        print(line)

    mx_ok = results["MX"][0] == PASS
    spf_ok = results["SPF"][0] == PASS
    return 0 if (mx_ok and spf_ok) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
