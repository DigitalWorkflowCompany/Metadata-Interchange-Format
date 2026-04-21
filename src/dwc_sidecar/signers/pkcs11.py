"""PKCS11Signer — Ed25519 signing via any PKCS#11 v3.0-compliant token.

Covers YubiHSM 2, Nitrokey HSM 2, SoftHSM (local testing), Thales Luna,
AWS CloudHSM, Entrust nShield, and most vendor HSMs. The private key
never leaves the token — only the canonical bytes go in, the signature
comes out.

Optional dependency: `pip install dwc-sidecar[hsm]`.

signers.json entry:

  {
    "type":        "pkcs11",
    "module":      "/usr/local/lib/libykcs11.dylib",
    "slot":        0,
    "label":       "dwc-dit-01",
    "pin_env":     "DWC_PKCS11_PIN"
  }

Either `slot` (integer index) or `token_label` (string) selects the token;
`label` selects the key object within the token (defaults to the kid);
`pin_env` names the env var holding the user PIN (defaults to DWC_PKCS11_PIN).

Ed25519 support requires PKCS#11 v3.0's EDDSA mechanism. Older vendor
libraries may expose the mechanism under a proprietary CKM_* constant —
if your HSM rejects CKM_EDDSA, check its documentation for the correct
mechanism name and wire it through `mechanism`.
"""
import os
from pathlib import Path

from .base import Signer


class PKCS11Signer(Signer):

    def __init__(
        self,
        kid: str,
        module: str,
        slot: int | None = None,
        token_label: str | None = None,
        label: str | None = None,
        pin_env: str = "DWC_PKCS11_PIN",
        pin: str | None = None,
        mechanism: str = "EDDSA",
    ):
        try:
            import pkcs11  # type: ignore[import-not-found]
            from pkcs11 import Mechanism, ObjectClass, Attribute  # type: ignore[import-not-found]
        except ImportError as e:
            raise ImportError(
                "PKCS11Signer requires the 'python-pkcs11' package. "
                "Install with:  pip install dwc-sidecar[hsm]"
            ) from e

        self._kid = kid
        self._mechanism_name = mechanism

        lib = pkcs11.lib(str(Path(module).expanduser()))

        if token_label is not None:
            token = lib.get_token(token_label=token_label)
        else:
            slots = lib.get_slots(token_present=True)
            if slot is None:
                if not slots:
                    raise RuntimeError(f"no tokens present in PKCS#11 module {module}")
                token = slots[0].get_token()
            else:
                token = slots[slot].get_token()

        resolved_pin = pin if pin is not None else os.environ.get(pin_env)
        if resolved_pin is None:
            raise RuntimeError(
                f"PKCS#11 PIN not provided: set env var {pin_env!r} "
                f"or pass `pin` in signers.json"
            )

        self._session  = token.open(user_pin=resolved_pin)
        key_label      = label or kid
        self._priv_key = self._session.get_key(
            object_class=ObjectClass.PRIVATE_KEY,
            label=key_label,
        )
        pub = self._session.get_key(
            object_class=ObjectClass.PUBLIC_KEY,
            label=key_label,
        )
        # EC_POINT on Ed25519 tokens is a DER OCTET STRING wrapping the raw
        # 32-byte key. Strip the DER header if present (0x04 0x20 ...).
        raw = bytes(pub[Attribute.EC_POINT])
        if len(raw) == 34 and raw[0] == 0x04 and raw[1] == 0x20:
            raw = raw[2:]
        self._pub_bytes = raw

        self._Mechanism = Mechanism

    def sign(self, message: bytes) -> bytes:
        mech = getattr(self._Mechanism, self._mechanism_name)
        return bytes(self._priv_key.sign(message, mechanism=mech))

    def public_key_bytes(self) -> bytes:
        return self._pub_bytes

    @property
    def kid(self) -> str:
        return self._kid

    def close(self) -> None:
        try:
            self._session.close()
        except Exception:
            pass
