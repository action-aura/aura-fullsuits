# Android Signing Key Backup Checklist

Losing these keys means losing the ability to ever update Retail or Clinic again for existing installs. Treat this checklist as non-optional.

## What must be backed up, as a set (never separate the keystore from its password)
- [ ] `retail-release.keystore` + the password that unlocks it
- [ ] `clinic-release.keystore` + the password that unlocks it
- [ ] A copy of `docs/release/android-production-signing-policy.md` (records the alias names and fingerprints needed to confirm a restored backup is the right one)

## Primary copy (already in place)
`C:\Users\Dell\AuraSigningKeys\` on this machine.

## Secondary copy — action needed from you
This wave generated the keystores but did **not** copy them to a second location — an automated attempt to sync them into a cloud-synced folder (OneDrive) was deliberately blocked by a safety control, since pushing a live production signing credential into cloud sync without a more careful (e.g. encrypted-archive) handling step is a real exposure risk that deserves a deliberate human decision, not an automated one.

Recommended options, in order of security (do at least one, ideally two, on physically or logically separate storage from this machine):
1. **Encrypted archive on external media**: create a password-protected 7-Zip/encrypted-zip of the `AuraSigningKeys` folder, store the archive on a USB drive kept somewhere other than this machine (e.g. a safe, a different physical location). The archive password should differ from the keystore passwords and be stored separately (e.g. in a password manager).
2. **Password manager with file-attachment support**: many password managers (1Password, Bitwarden with premium, etc.) support attaching files to a vault entry — this gives you encryption, versioning, and access from another device if this machine is lost.
3. **Cloud storage, but only as an encrypted archive**, never as raw files — if you do use OneDrive/Google Drive/etc., encrypt first (option 1's archive), then upload the archive, not the raw `.keystore`/`.pass` files.

## What NOT to do
- Do not email the keystore or password to yourself.
- Do not store the password in the same location as the keystore without at least one being encrypted (defeats the purpose of a "second factor" style split).
- Do not commit either file to any Git repository, even a private one.
- Do not store only on this one machine — a single-disk failure would be unrecoverable.

## Verifying a restored backup is correct
```
keytool -list -v -keystore retail-release.keystore -storepass <password>
```
Compare the printed SHA-256 fingerprint against `docs/release/android-production-signing-policy.md`. If it doesn't match exactly, the backup is not usable for signing updates to the already-published app.
