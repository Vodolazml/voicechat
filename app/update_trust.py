import base64
import json

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

RELEASE_PUBLIC_KEY = "HYepZEiOblbAVSsO/0XwR9gQZe4IivWmS+SArufRKtI="


def verify_release(version, url, sha256, signature):
    payload = json.dumps({'version': version, 'url': url, 'sha256': sha256.lower()},
                         sort_keys=True, separators=(',', ':')).encode()
    try:
        Ed25519PublicKey.from_public_bytes(base64.b64decode(RELEASE_PUBLIC_KEY)).verify(
            base64.b64decode(signature, validate=True), payload)
    except Exception as exc:
        raise ValueError("Недействительная подпись обновления") from exc
