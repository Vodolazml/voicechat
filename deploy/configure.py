"""Prepare an IP-based test deployment without printing credentials."""
import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import secrets
import shutil


def configure(root, server_ip, installer=None):
    ipaddress.IPv4Address(server_ip)
    env_path = root / '.env.ip'
    values = {}
    if env_path.exists():
        for line in env_path.read_text(encoding='utf-8-sig').splitlines():
            if '=' in line and not line.lstrip().startswith('#'):
                key, value = line.split('=', 1)
                values[key.strip()] = value.strip()
        backup = root / '.env.ip.backup'
        shutil.copy2(env_path, backup)
        os.chmod(backup, 0o600)
    values.setdefault('VOICECHAT_SECRET_KEY', secrets.token_urlsafe(48))
    values.setdefault('VOICECHAT_BOOTSTRAP_PASSWORD', secrets.token_urlsafe(20) + 'aA7!')
    values.setdefault('VOICECHAT_LIVEKIT_API_KEY', 'vc' + secrets.token_hex(12))
    values.setdefault('VOICECHAT_LIVEKIT_API_SECRET', secrets.token_urlsafe(48))
    values['VOICECHAT_LIVEKIT_URL'] = f'ws://{server_ip}:7880'
    values['VOICECHAT_ALLOWED_HOSTS'] = json.dumps([server_ip, '127.0.0.1', 'localhost'])
    values['VOICECHAT_CORS_ORIGINS'] = '[]'
    values['VOICECHAT_DOWNLOADS_DIR'] = '/data/downloads'
    if installer:
        path = root / 'downloads' / installer
        if path.parent.resolve() != (root / 'downloads').resolve() or not path.is_file():
            raise ValueError('Installer must exist in downloads/')
        manifest = json.loads(path.with_suffix('.release.json').read_text(encoding='utf-8'))
        with path.open('rb') as source:
            digest = hashlib.file_digest(source, 'sha256').hexdigest()
        if digest != manifest['sha256']:
            raise ValueError('Installer hash differs from release manifest')
        values['VOICECHAT_CLIENT_LATEST_VERSION'] = manifest['version']
        values['VOICECHAT_CLIENT_DOWNLOAD_URL'] = manifest['url']
        values['VOICECHAT_CLIENT_DOWNLOAD_SHA256'] = digest
        values['VOICECHAT_CLIENT_DOWNLOAD_SIGNATURE'] = manifest['signature']
        values['VOICECHAT_CLIENT_UPDATE_REQUIRED'] = 'true'
    env_path.write_text(''.join(f'{key}={value}\n' for key, value in values.items()), encoding='utf-8')
    os.chmod(env_path, 0o600)
    livekit = {
        'port': 7880,
        'rtc': {'tcp_port': 7881, 'udp_port': 7882, 'use_external_ip': True},
        'keys': {values['VOICECHAT_LIVEKIT_API_KEY']: values['VOICECHAT_LIVEKIT_API_SECRET']},
        'room': {'empty_timeout': 60, 'max_participants': 40},
    }
    target = root / 'deploy' / 'livekit.generated.yaml'
    target.write_text(json.dumps(livekit, indent=2), encoding='utf-8')
    os.chmod(target, 0o600)
    (root / 'downloads').mkdir(exist_ok=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('server_ip')
    parser.add_argument('--installer')
    args = parser.parse_args()
    configure(Path(__file__).resolve().parents[1], args.server_ip, args.installer)
    print('Updated .env.ip and LiveKit configuration. IP test mode uses HTTP; configure TLS before private use.')
