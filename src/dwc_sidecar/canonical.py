"""Canonicalization + hash/signature helpers for DWC events.

- canonical_bytes(obj): RFC 8785 JCS bytes of obj with 'hash' and 'sig' removed.
- event_hash(obj):       'sha256:<hex>' over canonical_bytes.
- sign_event(obj, priv): Ed25519 signature over canonical_bytes, base64.
- verify_event(obj, pub): True iff obj['hash'] matches recomputation AND
                          obj['sig'].value verifies against canonical_bytes.
"""
import base64, hashlib
import rfc8785  # type: ignore[import-not-found]
import xxhash   # type: ignore[import-not-found]
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey, Ed25519PublicKey,
)
from cryptography.exceptions import InvalidSignature

# blake3 is an optional dep at import time so the package loads in
# environments that don't ship it — notably Pyodide's package index at
# v0.27.3 has no blake3 wheel. Sidecars that declare blake3-hashed
# artifacts fail with a clear ImportError when the hasher is *used*
# (Stage 6/8), not at import time (plan §4.6).
try:
    import blake3  # type: ignore[import-not-found]
    _HAS_BLAKE3 = True
except ImportError:
    blake3 = None  # type: ignore[assignment]
    _HAS_BLAKE3 = False


# ASC MHL C4 ID (https://github.com/Avalanche-io/c4) — SHA-512 → base58 → "c4"-prefixed, padded to 90 chars.
_C4_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"

def _c4_encode(digest: bytes) -> str:
    n = int.from_bytes(digest, "big")
    out = ""
    while n:
        n, rem = divmod(n, 58)
        out = _C4_ALPHABET[rem] + out
    out = out.rjust(88, "1")  # 88 base58 chars from 64-byte SHA-512
    return "c4" + out


class _HasherBase:
    """Thin wrapper so non-hashlib algs (xxhash, blake3, c4) share one interface."""
    def __init__(self): self._h = self._make()
    def update(self, b: bytes): self._h.update(b)
    def hexdigest(self) -> str: return self._h.hexdigest()
    def _make(self): raise NotImplementedError

class _Xxh64(_HasherBase):
    def _make(self): return xxhash.xxh64()
class _Xxh3(_HasherBase):
    def _make(self): return xxhash.xxh3_64()
class _Blake3(_HasherBase):
    def _make(self):
        if not _HAS_BLAKE3 or blake3 is None:
            raise ImportError(
                "blake3 not available in this environment — install via "
                "`pip install blake3`, or use the CLI instead of the web "
                "validator for sidecars that declare blake3-hashed artifacts."
            )
        return blake3.blake3()  # type: ignore[union-attr]
    def hexdigest(self) -> str: return self._h.hexdigest()

class _C4:
    """Computes SHA-512 under the hood; hexdigest() returns C4 base58 form, not hex."""
    def __init__(self): self._h = hashlib.sha512()
    def update(self, b: bytes): self._h.update(b)
    def hexdigest(self) -> str: return _c4_encode(self._h.digest())


HASH_ALGS = {
    "md5":    hashlib.md5,
    "sha1":   hashlib.sha1,
    "sha256": hashlib.sha256,
    "sha512": hashlib.sha512,
    "blake3": _Blake3,
    "xxh64":  _Xxh64,
    "xxh3":   _Xxh3,
    "c4":     _C4,
}


def file_digest(path, alg: str) -> str:
    """Hash a file in streaming fashion with any registered algorithm."""
    hasher = HASH_ALGS[alg]()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _strip(ev: dict) -> dict:
    return {k: v for k, v in ev.items() if k not in ("hash", "sig")}


def canonical_bytes(ev: dict) -> bytes:
    return rfc8785.dumps(_strip(ev))


def event_hash(ev: dict) -> str:
    return "sha256:" + hashlib.sha256(canonical_bytes(ev)).hexdigest()


def sign_event(ev: dict, priv: Ed25519PrivateKey) -> str:
    return base64.b64encode(priv.sign(canonical_bytes(ev))).decode("ascii")


def verify_event(ev: dict, pub: Ed25519PublicKey) -> tuple[bool, str]:
    """Return (ok, reason). ok=True means hash matches AND signature verifies."""
    expected = event_hash(ev)
    if ev.get("hash") != expected:
        return False, f"hash mismatch: stored {ev.get('hash')!r}, recomputed {expected!r}"
    sig = ev.get("sig") or {}
    if sig.get("alg") != "ed25519":
        return False, f"unsupported sig.alg {sig.get('alg')!r}"
    try:
        pub.verify(base64.b64decode(sig["value"]), canonical_bytes(ev))
    except InvalidSignature:
        return False, "Ed25519 signature invalid"
    except Exception as e:
        return False, f"signature decode error: {e}"
    return True, "ok"


def load_pubkey_b64(b64: str) -> Ed25519PublicKey:
    return Ed25519PublicKey.from_public_bytes(base64.b64decode(b64))


def dump_pubkey_b64(pub: Ed25519PublicKey) -> str:
    from cryptography.hazmat.primitives import serialization
    raw = pub.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return base64.b64encode(raw).decode("ascii")
