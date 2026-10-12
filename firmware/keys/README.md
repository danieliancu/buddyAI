# firmware/keys — firmware signing key (NEVER COMMIT)

This directory holds the private key that signs BuddyAI release firmware
(`buddyai_signing_key.pem`, referenced by `CONFIG_SECURE_BOOT_SIGNING_KEY` in
`../sdkconfig.release` and `../sdkconfig.production`). `keys/.gitignore` ignores
everything here except this README and the `.gitignore` itself, and
`../.gitignore` ignores `*.pem`.

Whoever holds this key can push firmware to every watch in the field. Treat it
like a root password. Production units (Secure Boot V2, `../README.md` §8) run
**only** firmware signed with it, for the rest of their life.

## Who has access

- At most two named key holders (e.g. the founder and one deputy), written down
  with the date they got access. Nobody else, including contractors and CI.
- Release builds are made by a key holder on a trusted machine that is used
  only for that, ideally offline while the key is present.
- Keep a short log: date, firmware version signed, who signed, where the key
  was taken from and put back.
- When a key holder leaves, treat the key as possibly leaked (see below).

## Generate (once, on an offline machine)

From an ESP-IDF 5.5 shell on a machine that is **not connected to a network**:

```
espsecure.py generate_signing_key --version 2 --scheme rsa3072 keys/buddyai_signing_key.pem
```

(Run from `firmware/`. The ESP32-S3 Secure Boot V2 scheme is RSA-3072.)
Record the public key digest straight away (see the last section).

## Storage: two encrypted copies, two places

- Copy the key onto **two encrypted** media (e.g. hardware-encrypted USB drives,
  or VeraCrypt/LUKS/BitLocker volumes) with a strong passphrase, then delete it
  from the generating machine.
- Keep the two copies in **different physical places** (e.g. office safe and a
  bank deposit box or a key holder's home safe), so one fire or theft cannot
  take both.
- Store the passphrase separately from the media (password manager of the key
  holders, plus a sealed paper copy with the second medium).
- Check once a year that both copies still open and give the same digest.
- Copy the key into `firmware/keys/` only for the duration of a release build,
  then delete it from there.
- Never put it in the repository, CI (secrets of any CI service, including
  private ones), a cloud or shared drive, a server, e-mail, chat or a ticket.

The age identity that decrypts the server backups (`../../deploy/README.md`
§8.1) is handled the same way, as a separate key.

## Recovery (one copy lost or damaged)

1. Open the remaining copy on an offline machine and confirm its public key
   digest matches the recorded one.
2. Make a new second encrypted copy at once and store it in a different place
   than the surviving one.
3. If the lost copy may have been found by someone (stolen, not destroyed), the
   passphrase is the only protection left: follow "Leaked key" below.

## Losing / leaking the key

- **Lost key (both copies):** watches running a release build accept OTA updates
  only when they are signed with the same key as the running firmware. Without
  the key, unlocked units can only be updated over USB, and production units
  (Secure Boot) can **never** be updated again. This is why there are two copies.
- **Leaked key:** anyone can build firmware the watches accept. Generate a new
  key, ship a release signed with the *old* key that contains the new key
  (dual-signing / key rotation, see the ESP-IDF Secure Boot V2 docs), and
  revoke the old key digest on production units.

## Rotation and revocation (plan before production)

Secure Boot V2 can hold more than one public key digest in eFuse and supports
revoking a digest, which is what makes rotation after a leak possible. The exact
procedure (how many digests, when they must be burnt, which signatures the
images need, and whether a digest can still be added after a unit is locked in
release mode) is in the ESP-IDF Secure Boot V2 guide for ESP-IDF v5.5 and the
ESP32-S3; follow that guide, not memory. Before the first production run:

- decide whether production units get a **second (backup) key digest** at first
  boot, held by the other key holder, so a lost or revoked key does not strand
  the fleet;
- rehearse a rotation (sign with both keys, revoke one) on a **sacrificial**
  unit, and write the tested commands here;
- revoking burns an eFuse: irreversible, and a unit whose every key is revoked
  can never boot new firmware.

Planned rotation without a leak is not needed on a schedule; rotate when a key
holder leaves or the key may have been exposed.

## Public key digest (for records)

```
espsecure.py digest_sbv2_public_key --keyfile keys/buddyai_signing_key.pem --output keys/digest.bin
```

The digest is not secret: record its hex value (e.g.
`python -c "print(open('keys/digest.bin','rb').read().hex())"`) in the release
log, so any copy of the key can be checked against it.
