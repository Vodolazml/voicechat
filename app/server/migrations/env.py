from alembic import context

from app.server import models  # noqa: F401
from app.server.database import Base

target_metadata = Base.metadata
connection = context.config.attributes.get("connection")
if connection is None:
    from app.server.database import engine

    with engine.begin() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True)
        with context.begin_transaction():
            context.run_migrations()
else:
    context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()
