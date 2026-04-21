"""C4 interoperability test.

Compares canonical.py's C4 implementation against a verbatim transcription of
the Avalanche-io/pyc4 reference hash.py algorithm. Run with:  python3 test_c4_interop.py
"""
import hashlib
from canonical import _C4  # type: ignore[import-not-found]

_REF_CHARSET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def reference_c4(data: bytes) -> str:
    """Exact port of Avalanche-io/pyc4/hash.py algorithm to Python 3."""
    big = int.from_bytes(hashlib.sha512(data).digest(), "big")
    out = ["1"] * 90
    out[0], out[1] = "c", "4"
    for i in range(1, 89):
        if big <= 0:
            break
        big, rem = divmod(big, 58)
        out[90 - i] = _REF_CHARSET[rem]
    return "".join(out)


def _mine(data: bytes) -> str:
    h = _C4()
    h.update(data)
    return h.hexdigest()


VECTORS = [
    b"",
    b"foo",
    b"bar",
    b"The quick brown fox jumps over the lazy dog",
    bytes(range(256)),
    b"\x00" * 1024,
]


def run():
    failures = 0
    for v in VECTORS:
        r, m = reference_c4(v), _mine(v)
        ok = r == m
        label = v[:24].hex() if len(v) > 24 else v.hex() or "<empty>"
        print(f"  {label:<50} {'OK' if ok else 'DIFF'}")
        if not ok:
            failures += 1
            print(f"    ref : {r}")
            print(f"    ours: {m}")
    assert failures == 0, f"{failures} C4 mismatches vs reference"
    print("\n✓ C4 implementation matches Avalanche-io/pyc4 reference on all vectors")


if __name__ == "__main__":
    run()
