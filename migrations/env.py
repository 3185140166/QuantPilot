"""Use the same database configuration as API and workers."""

from alembic import context

from quantpilot.platform.database import make_database
from quantpilot.platform.models import Base
from quantpilot.platform.settings import PlatformSettings

settings = PlatformSettings.from_env()
if context.is_offline_mode():
    context.configure(
        url=settings.database_url, target_metadata=Base.metadata, literal_binds=True
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    engine, _ = make_database(settings.database_url)
    with engine.connect() as connection:
        context.configure(
            connection=connection, target_metadata=Base.metadata, compare_type=True
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()
