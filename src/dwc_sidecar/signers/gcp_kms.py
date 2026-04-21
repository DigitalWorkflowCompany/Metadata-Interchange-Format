"""GCPKMSSigner — Ed25519 signing via Google Cloud KMS.

GCP KMS added native Ed25519 support in 2023 (algorithm `EC_SIGN_ED25519`).
The private key never leaves Google's infrastructure; we send canonical
bytes and receive a signature.

Optional dependency: `pip install dwc-sidecar[gcp]`.

signers.json entry:

  {
    "type":        "gcp-kms",
    "key_version": "projects/<proj>/locations/<region>/keyRings/<ring>/cryptoKeys/<key>/cryptoKeyVersions/<v>"
  }

Credentials resolve through the google-cloud-kms default chain: the
`GOOGLE_APPLICATION_CREDENTIALS` env var → workload identity → metadata
service → gcloud auth application-default login.

Create the key externally (Terraform, gcloud, console). For a fresh Ed25519
signing key:

  gcloud kms keys create <name> \\
      --keyring <ring> --location <region> \\
      --purpose asymmetric-signing \\
      --default-algorithm ec-sign-ed25519
"""
from .base import Signer
from ._util import ed25519_pem_to_raw


class GCPKMSSigner(Signer):

    def __init__(self, kid: str, key_version: str):
        try:
            from google.cloud import kms  # type: ignore[import-not-found]
        except ImportError as e:
            raise ImportError(
                "GCPKMSSigner requires google-cloud-kms. "
                "Install with:  pip install dwc-sidecar[gcp]"
            ) from e

        self._kid         = kid
        self._key_version = key_version
        self._client      = kms.KeyManagementServiceClient()

        pub = self._client.get_public_key(request={"name": key_version})
        self._pub_bytes = ed25519_pem_to_raw(pub.pem)

    def sign(self, message: bytes) -> bytes:
        resp = self._client.asymmetric_sign(request={
            "name": self._key_version,
            "data": message,
        })
        return resp.signature

    def public_key_bytes(self) -> bytes:
        return self._pub_bytes

    @property
    def kid(self) -> str:
        return self._kid
