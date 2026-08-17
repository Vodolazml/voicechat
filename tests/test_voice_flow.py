from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.server import models
from app.server.database import Base
from app.server.main import voice_state_out
from app.server.security import hash_password


def test_voice_state_response_contains_display_name() -> None:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with session_factory() as db:
        admin = models.User(
            username="admin",
            display_name="Администратор",
            password_hash=hash_password("Admin12345!"),
        )
        db.add(admin)
        db.flush()
        state = models.VoiceState(user_id=admin.id, channel_id=1, muted=False, deafened=False)
        db.add(state)
        db.commit()

        response = voice_state_out(db, state)

    assert response.display_name == "Администратор"
    assert response.status == "connected"
