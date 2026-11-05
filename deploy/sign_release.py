import argparse
import base64
import hashlib
import json
import os
import re
from pathlib import Path
import sys

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization



def _sha256(source) -> str:
    """hashlib.file_digest needs Python 3.11; Ubuntu 22.04 ships 3.10."""
    digest = hashlib.sha256()
    for chunk in iter(lambda: source.read(1024 * 1024), b""):
        digest.update(chunk)
    return digest.hexdigest()

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
    parser.add_argument('--init-key', action='store_true',
                        help='First release only: create a signing key and make the client trust it')
    args = parser.parse_args()
    if args.init_key:
        if Path(args.key).is_file():
            parser.error('A signing key already exists; keep using it')
        key = load_key(Path(args.key))
        public = base64.b64encode(key.public_key().public_bytes(serialization.Encoding.Raw,
                                                                serialization.PublicFormat.Raw)).decode()
        trust = Path(__file__).resolve().parents[1] / 'app' / 'update_trust.py'
        text = trust.read_text(encoding='utf-8')
        trust.write_text(re.sub(r'RELEASE_PUBLIC_KEY = "[^"]*"', f'RELEASE_PUBLIC_KEY = "{public}"', text),
                         encoding='utf-8')
        print(f'Created {args.key} (back it up, never commit it). Client now trusts {public}')
        sys.exit(0)
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
            digest = _sha256(source)
        payload = {'version': args.version, 'url': args.url, 'sha256': digest}
        message = json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()
        payload['signature'] = base64.b64encode(key.sign(message)).decode()
        path.with_suffix('.release.json').write_text(json.dumps(payload, indent=2), encoding='utf-8')
        print(f'Signed {path.name}: {digest}')
