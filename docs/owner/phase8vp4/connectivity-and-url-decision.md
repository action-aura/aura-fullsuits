# Phase 8V-P4 — Connectivity and URL Decision

## URL semantics -- determined from source, not guessed

`commercial_runtime/licensing_contracts/client.py`:

```python
@dataclass
class LicensingClientConfig:
    base_url: str  # e.g. "https://licensing.example.internal/api/licensing/v1"
    ...

def _request(self, method, path, json_body):
    url = self._config.base_url.rstrip("/") + path
    ...

# call sites:
self._post_signed("/activations", body, signer)
self._post_signed("/check-ins", body, signer)
self._post_signed("/deactivations", body, signer)
self._get("/signing-keys")
self._get("/service-info")
```

**Mode B applies**: `base_url` must be the complete licensing namespace URL, including
`/api/licensing/v1`. `path` values (`/activations`, `/check-ins`, etc.) are simple concatenated
suffixes -- the client never appends `/api/licensing/v1` itself. This confirms the value already
used and documented in the Phase 8V-P/8V-P2/8V-P3 handovers is correct in form; it just was never
actually passed to a real build until this session.

## Connectivity mode chosen: **Mode 1, `adb reverse`**

Preferred per this phase's own guidance, and directly usable here since Owner was started bound to
`0.0.0.0:5551` (reachable on the host's own loopback either way).

```
$ adb reverse tcp:5551 tcp:5551
5551
$ adb reverse --list
UsbFfs tcp:5551 tcp:5551
```

Confirmed active. This maps the device's own `127.0.0.1:5551` to the host machine's `127.0.0.1:5551`
over the existing USB/ADB transport -- no LAN exposure, no firewall rule, no public network
involvement.

## Resolved Gradle property value

```
-PownerLicensingBaseUrl=http://127.0.0.1:5551/api/licensing/v1
```

`http`, not `https` -- this is a local validation-only loopback tunnel over USB, not a real network
path; the release APK's own TLS verification logic is not weakened or bypassed by this (no
certificate-trust code is touched), it is simply not exercised for this specific validation
connection, exactly the same tradeoff the Phase 8V-P Windows sessions already made and documented
(`AURA_OWNER_LICENSING_INSECURE=1` was that session's own equivalent local-loopback-only flag). No
`--insecure`/TLS-trust-all logic was added to any product source.

## Host-side reachability (proven)

```
$ curl -s http://127.0.0.1:5551/api/licensing/v1/service-info
{"active_signing_key_id": "owner-ed25519-20260727T053324Z-c32537d7", "service": "aura-owner-licensing", "status": "OK", "supported_contract_versions": ["v1"]}
$ curl -s http://127.0.0.1:5551/api/licensing/v1/signing-keys
{"issued_at": "...", "keys": [{"key_id": "owner-ed25519-20260727T053324Z-c32537d7", "status": "ACTIVE", ...}], "signature": "...", "signed_by_key_id": "owner-ed25519-20260727T053324Z-c32537d7"}
```

Both `/service-info` and `/signing-keys` respond correctly. `/activations` and `/check-ins` are
POST-only, signed-body endpoints with no safe no-op GET form to pre-probe -- their real reachability
and correctness is proven by the actual app performing a real activation in Part J, which is the
authoritative test for this connectivity path anyway.

## Device-side raw reachability check -- limited by toolbox, not a gap in the mechanism

Attempted `adb shell` with `toybox nc` to independently confirm the tunnel from the device's own
shell; the device's minimal Android toolbox environment doesn't provide a usable `curl` (`inaccessible
or not found`) and the available `toybox nc` didn't produce readable output over adb shell's own I/O
plumbing. `adb reverse` is a well-defined, deterministic kernel-level TCP forward (not something that
partially works) -- its `--list` confirmation plus the real, successful in-app activation call in
Part J together constitute sufficient proof of device-side reachability; a raw on-device curl would
have been a redundant pre-check, not additional evidence beyond what the real app call already
provides.

## Owner port and cleanup plan

Owner dev server: PID tracked this session, bound to `0.0.0.0:5551`, started via
`werkzeug.serving.make_server` against the real `aura_owner_dev` Postgres database. `adb reverse`
rule: `tcp:5551 tcp:5551`. No firewall rule was added (not needed for Mode 1). Cleanup at session end:
`adb reverse --remove tcp:5551`, stop the Owner dev process -- see `validation-cleanup.md`.
