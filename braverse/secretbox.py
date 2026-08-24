"""Authenticated encryption, built out of the standard library.

The engine's whole dependency budget is numpy, and a card game should not spend
it on `cryptography` to keep one small file of statistics private. So this is
the smallest construction that is actually sound, assembled from `hashlib` and
`hmac`:

* **The key comes from a passphrase via `hashlib.scrypt`** — memory-hard, so a
  guessing attack pays for every attempt in RAM as well as time. The salt is
  per-file and stored in the clear beside the ciphertext, which is what a salt
  is for.
* **The keystream is HMAC-SHA256 in counter mode.** ``HMAC(k, nonce || i)`` for
  i = 0, 1, 2, … is a PRF evaluated at distinct points; XORing it into the
  plaintext is a stream cipher of exactly the same shape as AES-CTR, with the
  block cipher swapped for a hash. Slower than AES-NI by a wide margin and
  entirely irrelevant at the size of a profile.
* **Encrypt-then-MAC.** The tag covers the header, the nonce and the
  ciphertext, and is checked *before* a single byte is decrypted. A file that
  has been edited — including one where only the cleartext header was edited —
  fails to open rather than opening as something else.

Two rules this file exists to hold in one place: the encryption key and the
MAC key are separate (both derived from the master key, so one HMAC never both
encrypts and authenticates), and the nonce is random per *save*, never reused
across two seals under one key.

    key  = derive("hunter2", salt)
    blob = seal(key, b"...", aad=header)
    same = unseal(key, blob, aad=header)      # BadSeal if either was touched
"""

from __future__ import annotations

import hashlib
import hmac
import os

MAGIC = b"BVSB1"          # bumped only if the wire format changes
NONCE_BYTES = 16
TAG_BYTES = 32
SALT_BYTES = 16
KEY_BYTES = 32

# scrypt cost. 128 * N * r bytes of memory — 16 MiB here, about a tenth of a
# second on a laptop. High enough that guessing a passphrase is expensive,
# low enough that unlocking a profile is not something you wait for.
SCRYPT_N = 1 << 14
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_MAXMEM = 64 << 20   # OpenSSL's default is 32 MiB; leave headroom


class BadSeal(Exception):
    """The blob is not what was sealed: wrong key, or edited since."""


def new_salt() -> bytes:
    return os.urandom(SALT_BYTES)


def new_key() -> bytes:
    """A master key that is not derived from anything — for the keyfile."""
    return os.urandom(KEY_BYTES)


def derive(passphrase: str, salt: bytes) -> bytes:
    """A master key from a passphrase and its salt."""
    return hashlib.scrypt(passphrase.encode("utf-8"), salt=salt,
                          n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P,
                          maxmem=SCRYPT_MAXMEM, dklen=KEY_BYTES)


def subkey(key: bytes, label: bytes) -> bytes:
    """Split the master key. The two uses must never share one key."""
    return hmac.new(key, label, hashlib.sha256).digest()


def _keystream(key: bytes, nonce: bytes, length: int) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < length:
        out += hmac.new(key, nonce + counter.to_bytes(8, "big"),
                        hashlib.sha256).digest()
        counter += 1
    return bytes(out[:length])


def _tag(key: bytes, aad: bytes, nonce: bytes, body: bytes) -> bytes:
    # The length prefix is not decoration: without it, moving a byte from the
    # end of `aad` to the front of `nonce` would leave the tag unchanged.
    mac = hmac.new(key, MAGIC, hashlib.sha256)
    mac.update(len(aad).to_bytes(8, "big"))
    mac.update(aad)
    mac.update(nonce)
    mac.update(body)
    return mac.digest()


def seal(key: bytes, plaintext: bytes, *, aad: bytes = b"") -> bytes:
    """``MAGIC || nonce || ciphertext || tag``.

    `aad` is authenticated but not encrypted — the cleartext header of a
    profile file goes here, so the name on the outside cannot be swapped for
    another without the file refusing to open.
    """
    nonce = os.urandom(NONCE_BYTES)
    body = bytes(a ^ b for a, b in
                 zip(plaintext, _keystream(subkey(key, b"enc"), nonce,
                                           len(plaintext))))
    tag = _tag(subkey(key, b"mac"), aad, nonce, body)
    return MAGIC + nonce + body + tag


def unseal(key: bytes, blob: bytes, *, aad: bytes = b"") -> bytes:
    """The plaintext, or `BadSeal`. The tag is checked before anything else."""
    head = len(MAGIC) + NONCE_BYTES
    if len(blob) < head + TAG_BYTES or not blob.startswith(MAGIC):
        raise BadSeal("not a sealed file")
    nonce = blob[len(MAGIC):head]
    body, tag = blob[head:-TAG_BYTES], blob[-TAG_BYTES:]
    want = _tag(subkey(key, b"mac"), aad, nonce, body)
    # Constant-time: the tag is the only thing standing between an edited file
    # and being believed.
    if not hmac.compare_digest(tag, want):
        raise BadSeal("wrong passphrase, or the file has been edited")
    return bytes(a ^ b for a, b in
                 zip(body, _keystream(subkey(key, b"enc"), nonce, len(body))))
