import base64
import json

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

from app import update_trust


def test_release_signature_binds_url_version_and_hash(monkeypatch):
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    monkeypatch.setattr(update_trust, 'RELEASE_PUBLIC_KEY', base64.b64encode(public).decode())
    payload = {'version': '0.2.0', 'url': 'https://example.test/client.exe', 'sha256': 'a' * 64}
    signature = base64.b64encode(key.sign(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode())).decode()
    update_trust.verify_release(**payload, signature=signature)
    with pytest.raises(ValueError):
        update_trust.verify_release(**{**payload, 'url': 'https://other.test/client.exe'}, signature=signature)
    with pytest.raises(ValueError):
        update_trust.verify_release(**{**payload, 'version': '0.1.0'}, signature=signature)
