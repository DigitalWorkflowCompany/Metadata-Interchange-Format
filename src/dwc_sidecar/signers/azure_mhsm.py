"""AzureManagedHSMSigner — Ed25519 signing via Azure Key Vault Managed HSM.

Ed25519 (`EDDSA`) is supported only on the **Managed HSM** tier of Azure
Key Vault — not standard Key Vault. Keys are FIPS 140-3 Level 3 protected;
private key material never leaves the HSM.

Optional dependency: `pip install dwc-sidecar[azure]`.

signers.json entry:

  {
    "type":   "azure-mhsm",
    "key_id": "https://<hsm-name>.managedhsm.azure.net/keys/<key-name>/<version>"
  }

Auth resolves through `DefaultAzureCredential`: env vars, managed identity,
Azure CLI, Visual Studio Code, PowerShell — whichever is available.

Create the key externally:

  az keyvault key create \\
      --hsm-name <hsm-name> \\
      --name dwc-color-01 \\
      --kty OKP \\
      --curve Ed25519 \\
      --ops sign verify
"""
from .base import Signer


class AzureManagedHSMSigner(Signer):

    def __init__(self, kid: str, key_id: str):
        try:
            from azure.identity import DefaultAzureCredential  # type: ignore[import-not-found]
            from azure.keyvault.keys import KeyClient  # type: ignore[import-not-found]
            from azure.keyvault.keys.crypto import (  # type: ignore[import-not-found]
                CryptographyClient, SignatureAlgorithm,
            )
        except ImportError as e:
            raise ImportError(
                "AzureManagedHSMSigner requires azure-keyvault-keys + azure-identity. "
                "Install with:  pip install dwc-sidecar[azure]"
            ) from e

        self._kid       = kid
        self._key_id    = key_id
        self._eddsa     = SignatureAlgorithm.eddsa
        credential      = DefaultAzureCredential()
        self._crypto    = CryptographyClient(key_id, credential)

        # Derive vault URL + key name/version from the full key_id URL.
        # Format: https://<hsm>.managedhsm.azure.net/keys/<name>/<version>
        head, _, tail = key_id.partition("/keys/")
        parts = tail.split("/", 1)
        name    = parts[0]
        version = parts[1] if len(parts) > 1 else None
        key_client = KeyClient(vault_url=head, credential=credential)
        jwk = key_client.get_key(name, version=version).key
        # For Ed25519 (`kty=OKP`, `crv=Ed25519`), JWK `.x` holds the raw 32-byte
        # public key — either as bytes or base64url-decoded bytes, depending on
        # SDK version.
        x = jwk.x
        if isinstance(x, str):
            import base64
            pad = "=" * (-len(x) % 4)
            x = base64.urlsafe_b64decode(x + pad)
        self._pub_bytes = bytes(x)

    def sign(self, message: bytes) -> bytes:
        resp = self._crypto.sign(self._eddsa, message)
        return bytes(resp.signature)

    def public_key_bytes(self) -> bytes:
        return self._pub_bytes

    @property
    def kid(self) -> str:
        return self._kid
