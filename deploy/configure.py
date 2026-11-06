"""Generate server secrets and LiveKit configuration for a domain deployment.

Run by deploy/install.sh; safe to run again: existing secrets are kept, only the domain-derived
values and the client release fields are refreshed. Never prints secrets.
"""
import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
from pathlib import Path

DOMAIN_RE = re.compile(r"^(?=.{4,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
UDP_RANGE = (50000, 60000)



def _sha256(source) -> str:
    """hashlib.file_digest needs Python 3.11; Ubuntu 22.04 ships 3.10."""
    digest = hashlib.sha256()
    for chunk in iter(lambda: source.read(1024 * 1024), b""):
        digest.update(chunk)
    return digest.hexdigest()

def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip()
    return values


def write_private(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    os.chmod(path, 0o600)


def configure(root: Path, domain: str, email: str = "", installer: str | None = None) -> dict[str, str]:
    domain = domain.strip().lower().rstrip(".")
    if not DOMAIN_RE.match(domain):
        raise ValueError(f"Not a valid domain name: {domain!r}")
    env_path = root / ".env.production"
    values = read_env(env_path)
    if env_path.exists():
        backup = root / ".env.production.backup"
        shutil.copy2(env_path, backup)
        os.chmod(backup, 0o600)

    values["VOICECHAT_ENVIRONMENT"] = "production"
    values["VOICECHAT_DOMAIN"] = domain
    if email:
        values["VOICECHAT_ACME_EMAIL"] = email
    values.setdefault("VOICECHAT_ACME_EMAIL", "")
    values.setdefault("VOICECHAT_SECRET_KEY", secrets.token_urlsafe(48))
    # Empty: the server generates a one-time owner password on first start and prints it to its log.
    values.setdefault("VOICECHAT_BOOTSTRAP_PASSWORD", "")
    values.setdefault("POSTGRES_USER", "voicechat")
    values.setdefault("POSTGRES_DB", "voicechat")
    values.setdefault("POSTGRES_PASSWORD", secrets.token_urlsafe(32))
    values["VOICECHAT_DATABASE_URL"] = (
        f"postgresql+psycopg://{values['POSTGRES_USER']}:{values['POSTGRES_PASSWORD']}"
        f"@127.0.0.1:5432/{values['POSTGRES_DB']}"
    )
    values.setdefault("VOICECHAT_LIVEKIT_API_KEY", "vc" + secrets.token_hex(12))
    values.setdefault("VOICECHAT_LIVEKIT_API_SECRET", secrets.token_urlsafe(48))
    values["VOICECHAT_LIVEKIT_URL"] = f"wss://rtc.{domain}"
    values["VOICECHAT_LIVEKIT_INTERNAL_URL"] = "http://127.0.0.1:7880"
    values["VOICECHAT_ALLOWED_HOSTS"] = json.dumps([f"chat.{domain}", "127.0.0.1", "localhost"])
    values["VOICECHAT_CORS_ORIGINS"] = "[]"
    values["VOICECHAT_DOWNLOADS_DIR"] = "/data/downloads"

    if installer:
        downloads = (root / "downloads").resolve()
        path = (downloads / installer).resolve()
        if path.parent != downloads or not path.is_file():
            raise ValueError("Installer must exist in downloads/")
        manifest = json.loads(path.with_suffix(".release.json").read_text(encoding="utf-8"))
        with path.open("rb") as source:
            digest = _sha256(source)
        if digest != manifest["sha256"]:
            raise ValueError("Installer hash differs from release manifest")
        values["VOICECHAT_CLIENT_LATEST_VERSION"] = manifest["version"]
        values["VOICECHAT_CLIENT_DOWNLOAD_URL"] = f"https://chat.{domain}/downloads/{path.name}"
        values["VOICECHAT_CLIENT_DOWNLOAD_SHA256"] = digest
        values["VOICECHAT_CLIENT_DOWNLOAD_SIGNATURE"] = manifest["signature"]

    write_private(env_path, "".join(f"{key}={value}\n" for key, value in values.items()))

    livekit = {
        "port": 7880,
        "rtc": {
            "tcp_port": 7881,
            "port_range_start": UDP_RANGE[0],
            "port_range_end": UDP_RANGE[1],
            "use_external_ip": True,
        },
        # Relay for friends behind strict NAT; LiveKit authenticates TURN with the room token.
        "turn": {"enabled": True, "domain": f"rtc.{domain}", "udp_port": 3478},
        "keys": {values["VOICECHAT_LIVEKIT_API_KEY"]: values["VOICECHAT_LIVEKIT_API_SECRET"]},
        # Signed callbacks: the API learns instantly when someone's connection closes.
        "webhook": {
            "api_key": values["VOICECHAT_LIVEKIT_API_KEY"],
            "urls": ["http://127.0.0.1:8765/livekit/webhook"],
        },
        "room": {"empty_timeout": 60, "departure_timeout": 15, "max_participants": 40},
        "logging": {"level": "info"},
    }
    write_private(root / "deploy" / "livekit.generated.yaml", json.dumps(livekit, indent=2))
    (root / "downloads").mkdir(exist_ok=True)
    return values


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("domain", help="Your domain, e.g. example.org (chat.* and rtc.* subdomains are used)")
    parser.add_argument("--email", default="", help="Contact e-mail for Let's Encrypt")
    parser.add_argument("--installer", help="Client installer file in downloads/ to publish as the latest version")
    args = parser.parse_args()
    configure(Path(__file__).resolve().parents[1], args.domain, args.email, args.installer)
    print("Updated .env.production and deploy/livekit.generated.yaml (secrets are not printed).")
