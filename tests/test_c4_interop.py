"""C4 interoperability: our implementation vs a verbatim transcription of
the Avalanche-io/pyc4 reference algorithm."""
import hashlib

import pytest

from dwc_sidecar.canonical import _C4

_REF_CHARSET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def _reference_c4(data: bytes) -> str:
    """Exact port of Avalanche-io/pyc4/hash.py algorithm."""
    big = int.from_bytes(hashlib.sha512(data).digest(), "big")
    out = ["1"] * 90
    out[0], out[1] = "c", "4"
    for i in range(1, 89):
        if big <= 0:
            break
        big, rem = divmod(big, 58)
        out[90 - i] = _REF_CHARSET[rem]
    return "".join(out)


def _ours(data: bytes) -> str:
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


@pytest.mark.parametrize("data", VECTORS, ids=lambda d: d[:16].hex() or "empty")
def test_matches_reference(data):
    assert _ours(data) == _reference_c4(data)
