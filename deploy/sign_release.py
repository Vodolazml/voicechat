import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import sys

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization


def load_key(path):
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        key = Ed25519PrivateKey.generate()
        path.write_bytes(key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                          serialization.NoEncryption()))
        os.chmod(path, 0o600)
    return Ed25519PrivateKey.from_private_bytes(path.read_bytes())


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--key', default='release-signing.key')
    parser.add_argument('--file')
    parser.add_argument('--version')
    parser.add_argument('--url')
    args = parser.parse_args()
    if args.file and not Path(args.key).is_file():
        parser.error('Release signing key is missing; restore the original key from backup')
    key = load_key(Path(args.key))
    public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    if not args.file:
        print(base64.b64encode(public).decode())
    else:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from app.update_trust import RELEASE_PUBLIC_KEY
        if base64.b64encode(public).decode() != RELEASE_PUBLIC_KEY:
            parser.error('Signing key does not match the trusted client public key')
        path = Path(args.file)
        with path.open('rb') as source:
            digest = hashlib.file_digest(source, 'sha256').hexdigest()
        payload = {'version': args.version, 'url': args.url, 'sha256': digest}
        message = json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()
        payload['signature'] = base64.b64encode(key.sign(message)).decode()
        path.with_suffix('.release.json').write_text(json.dumps(payload, indent=2), encoding='utf-8')
        print(f'Signed {path.name}: {digest}')
