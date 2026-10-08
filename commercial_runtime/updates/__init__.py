"""Update channel (product side) -- find, download, verify and launch a newer
installer for the Windows desktop app.

Why this package exists: the README used to say "There is no update channel. A
later build cannot reach an existing install by itself." Owner already stores
published releases and serves them through a device-signed, single-use download
token; what was missing was the product-side client. Nothing here changes a
schema or touches retail.db -- state lives in <app_data>/updates/state.json.

Trust model, in one paragraph: the answer to "is there a newer version, and
what are its checksum and size?" is an Owner-SIGNED descriptor verified against
the same OwnerTrustStore the licence uses. The downloaded bytes are hashed
against THAT signed checksum -- never against the number in the download
response, which is not signed -- and are hashed again immediately before the
installer is launched, so a file swapped on disk in between is refused.
"""
