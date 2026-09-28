# firmware/keys — firmware signing key (NEVER COMMIT)

This directory holds the private key that signs BuddyAI release firmware
(`buddyai_signing_key.pem`, referenced by `CONFIG_SECURE_BOOT_SIGNING_KEY` in
`../sdkconfig.release`). `keys/.gitignore` ignores everything here except this
README and the `.gitignore` itself, and `../.gitignore` ignores `*.pem`.

Whoever holds this key can push firmware to every watch in the field. Treat it
like a root password.

## Generate (once, on an offline machine)

From an ESP-IDF 5.5 shell:

```
espsecure.py generate_signing_key --version 2 --scheme rsa3072 keys/buddyai_signing_key.pem
```

(Run from `firmware/`. The ESP32-S3 Secure Boot V2 scheme is RSA-3072.)

Keep the key **offline**:

- store it on an encrypted USB drive (plus one backup copy in a safe place);
- copy it into `firmware/keys/` only for the duration of a release build, then delete it;
- never put it in the repository, CI secrets of a public project, a shared drive, e-mail or chat.

## Losing / leaking the key

- **Lost key:** watches running a release build accept OTA updates only when
  they are signed with the same key as the running firmware. Without the key,
  units in the field can only be updated over USB (and, once Secure Boot is
  enabled on a production unit, not at all with a different key). Keep a backup.
- **Leaked key:** anyone can build firmware the watches accept. Generate a new
  key, ship a release signed with the *old* key that contains the new key
  (dual-signing / key rotation, see the ESP-IDF Secure Boot V2 docs), and
  revoke the old key digest on production units.

## Public key digest (for records)

```
espsecure.py digest_sbv2_public_key --keyfile keys/buddyai_signing_key.pem --output keys/digest.bin
```
