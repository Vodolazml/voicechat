from tests.test_presence import client  # noqa: F401
from datetime import timedelta

from app.server import legal, models
from app.server import main


def _register(client, username, accept=True):
    return client.post("/auth/register", json={"accept_terms": accept, "username": username,
                                               "display_name": username.title(), "password": "Strong-Pass-123!"})


def test_legal_pages_are_public_and_filled_in(client):
    for page, words in (("privacy", "Политика конфиденциальности"), ("terms", "Пользовательское соглашение"),
                        ("consent", "Согласие на обработку"), ("licenses", "Лицензии")):
        response = client.get(f"/{page}")
        assert response.status_code == 200, page
        assert words in response.text
        assert "{operator}" not in response.text and "{server}" not in response.text
    assert "/privacy" in client.get("/").text


def test_sign_up_needs_the_consent_and_records_it(client):
    refused = _register(client, "nocheck", accept=False)
    assert refused.status_code == 400
    token = _register(client, "agreed").json()["access_token"]
    me = client.get("/me", headers={"Authorization": f"Bearer {token}"}).json()
    assert me["consent_version"] == legal.LEGAL_VERSION == me["consent_required"]


def test_old_account_accepts_the_new_edition(client):
    token = _register(client, "oldie").json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    with main.SessionLocal() as db:
        db.query(models.User).filter_by(username="oldie").update({"consent_version": 0})
        db.commit()
    assert client.get("/me", headers=headers).json()["consent_version"] == 0
    assert client.post("/me/consent", headers=headers, json={"version": legal.LEGAL_VERSION}).status_code == 200
    assert client.get("/me", headers=headers).json()["consent_version"] == legal.LEGAL_VERSION


def test_deleting_the_account_wipes_personal_data(client):
    token = _register(client, "leaver").json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    assert client.post("/me/delete", headers=headers, json={"password": "wrong"}).status_code == 400
    assert client.post("/me/delete", headers=headers, json={"password": "Strong-Pass-123!"}).status_code == 200
    assert client.get("/me", headers=headers).status_code == 401
    with main.SessionLocal() as db:
        user = db.query(models.User).filter_by(username="leaver").first()
        assert user is None
        gone = db.query(models.User).filter(models.User.username.like("deleted-%")).one()
        assert gone.status == "deleted" and gone.display_name == "Удалённый пользователь"
        assert not db.query(models.LoginSession).filter_by(user_id=gone.id).count()
    assert client.post("/auth/login", json={"username": "leaver", "password": "Strong-Pass-123!"}).status_code == 401
    assert _register(client, "leaver").status_code == 200, "the login is free again"


def test_old_sign_ins_and_audit_records_are_purged(client):
    _register(client, "keeper")
    with main.SessionLocal() as db:
        long_ago = models.utcnow() - timedelta(days=400)
        db.query(models.LoginSession).update({"expires_at": long_ago})
        db.add(models.AuditLog(action="old", target_type="x", target_id="1", created_at=long_ago))
        db.commit()
        sessions, audits = legal.purge_old_records(db)
        assert sessions >= 1 and audits >= 1
        assert not db.query(models.AuditLog).filter_by(action="old").count()


def test_markdown_renders_lists_and_bold():
    html = legal.render_markdown("# T\n\nText **bold**\n\n- a\n- b\n\n1. one\n")
    assert "<h1>T</h1>" in html and "<b>bold</b>" in html and "<ul>" in html and "<ol>" in html
