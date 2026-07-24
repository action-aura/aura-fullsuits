# Phase 7V — Android Physical Signed Upgrade Evidence (Part K)

## Status: NOT VERIFIED — no physical device connected

`adb devices -l` returned an empty list at every check performed this session (baseline, before
Part H, and immediately before Part J). Per the governing spec's Part J instruction: "If no
physical device is connected: stop the mandatory physical-validation portion, tell the user to
connect and authorize the device, do not fabricate results, do not substitute emulator results, do
not create the final Phase 7V checkpoint tag."

No physical rc.1→rc.2 signed-upgrade install was attempted. No emulator substitution was used
either, per the same instruction (emulator testing is explicitly supplemental-only, not a
substitute for this mandatory part).

## What is available as indirect evidence

- **Certificate continuity** (`android-certificate-continuity.md`): the signer identity Android's
  package installer checks before accepting an upgrade is confirmed unchanged for both products.
- **Package ID continuity**: confirmed unchanged (`android-certificate-continuity.md`).
- These two facts together are necessary for a signed upgrade to be *possible*, but they are not a
  substitute for actually performing the upgrade install on real hardware and observing Android
  accept it, the app launch, and data survive.

## What remains to be done

Connect and authorize a physical Android device (`adb devices -l` must show it), then:
1. Install the real rc.1 signed APK.
2. Create representative data.
3. Install the rc.2 signed APK over it (`adb install -r`, or a manual APK transfer + install to
   more closely match a real user's upgrade path).
4. Confirm Android accepts the upgrade (no uninstall-then-reinstall prompt, no signature-mismatch
   error).
5. Confirm data and package signer survive.

## Verdict

**NOT VERIFIED.** This is a mandatory gate per the spec's Definition of Done — its absence alone
means the final Phase 7V closing tag cannot be created this session.
