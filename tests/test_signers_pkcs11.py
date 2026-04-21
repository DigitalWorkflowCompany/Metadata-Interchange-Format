"""PKCS11Signer — python-pkcs11 mocked via sys.modules.

The fake pkcs11 module drives a real Ed25519 key so verify() catches wiring
errors. We also test the EC_POINT DER-unwrap path used for common vendor
PKCS#11 libraries."""
import sys
import types
from unittest.mock import MagicMock

import pytest



def _install_pkcs11_mock(monkeypatch, priv, pub_raw, *, ec_point_der_wrap: bool = False):
    """Replace 'pkcs11' in sys.modules with a fake that returns signatures from `priv`."""
    mock_priv_key = MagicMock()
    mock_priv_key.sign.side_effect = lambda msg, mechanism: priv.sign(msg)

    mock_pub_key = MagicMock()
    # Vendors commonly DER-wrap Ed25519 EC_POINT as 0x04 0x20 <32 raw bytes>.
    point = (b"\x04\x20" + pub_raw) if ec_point_der_wrap else pub_raw
    mock_pub_key.__getitem__.return_value = point

    mock_session = MagicMock()
    # session.get_key(object_class=..., label=...) → priv or pub depending on object_class
    def _get_key(object_class, label):
        return mock_priv_key if object_class == "priv" else mock_pub_key
    mock_session.get_key.side_effect = _get_key

    mock_token = MagicMock()
    mock_token.open.return_value = mock_session
    mock_slot = MagicMock()
    mock_slot.get_token.return_value = mock_token

    mock_lib = MagicMock()
    mock_lib.get_slots.return_value = [mock_slot]
    mock_lib.get_token.return_value = mock_token

    fake_pkcs11 = types.ModuleType("pkcs11")
    fake_pkcs11.lib = MagicMock(return_value=mock_lib)  # type: ignore[attr-defined]
    class _ObjectClass:
        PRIVATE_KEY = "priv"
        PUBLIC_KEY  = "pub"
    class _Attribute:
        EC_POINT = "ec-point-attr"
    class _Mechanism:
        EDDSA = "eddsa-mech"
    fake_pkcs11.ObjectClass = _ObjectClass  # type: ignore[attr-defined]
    fake_pkcs11.Attribute   = _Attribute    # type: ignore[attr-defined]
    fake_pkcs11.Mechanism   = _Mechanism    # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "pkcs11", fake_pkcs11)
    monkeypatch.delitem(sys.modules, "dwc_sidecar.signers.pkcs11", raising=False)

    return mock_priv_key, mock_session


def test_pkcs11_signer_roundtrip_raw_point(monkeypatch, ed25519_keypair, verify_signature):
    priv, pub_raw, _ = ed25519_keypair
    _, _ = _install_pkcs11_mock(monkeypatch, priv, pub_raw, ec_point_der_wrap=False)
    monkeypatch.setenv("DWC_PKCS11_PIN", "1234")

    from dwc_sidecar.signers.pkcs11 import PKCS11Signer
    s = PKCS11Signer("dwc-dit-01", module="/path/to/lib.so", slot=0, label="dwc-dit-01")

    assert s.kid == "dwc-dit-01"
    assert s.public_key_bytes() == pub_raw
    sig = s.sign(b"hi")
    verify_signature(pub_raw, b"hi", sig)


def test_pkcs11_signer_unwraps_der_ec_point(monkeypatch, ed25519_keypair):
    """Some vendor libraries return EC_POINT as DER OCTET STRING (04 20 <raw>)."""
    priv, pub_raw, _ = ed25519_keypair
    _install_pkcs11_mock(monkeypatch, priv, pub_raw, ec_point_der_wrap=True)
    monkeypatch.setenv("DWC_PKCS11_PIN", "1234")

    from dwc_sidecar.signers.pkcs11 import PKCS11Signer
    s = PKCS11Signer("x", module="/lib.so", slot=0)
    assert s.public_key_bytes() == pub_raw


def test_pkcs11_signer_missing_pin_raises(monkeypatch, ed25519_keypair):
    priv, pub_raw, _ = ed25519_keypair
    _install_pkcs11_mock(monkeypatch, priv, pub_raw)
    monkeypatch.delenv("DWC_PKCS11_PIN", raising=False)

    from dwc_sidecar.signers.pkcs11 import PKCS11Signer
    with pytest.raises(RuntimeError, match="PKCS#11 PIN not provided"):
        PKCS11Signer("x", module="/lib.so", slot=0)


def test_pkcs11_signer_missing_lib_raises(monkeypatch):
    monkeypatch.setitem(sys.modules, "pkcs11", None)
    monkeypatch.delitem(sys.modules, "dwc_sidecar.signers.pkcs11", raising=False)

    from dwc_sidecar.signers.pkcs11 import PKCS11Signer
    with pytest.raises(ImportError, match="dwc-sidecar\\[hsm\\]"):
        PKCS11Signer("x", module="/lib.so", slot=0)
