"""The Compass store: ports (``ports``), an in-memory fake (``memory``) and PostgreSQL (``postgres``),
validated by one shared contract suite (``contract``). Tables are prefixed ``compass_``; the schema is
managed by this package's own Alembic history (``migrate``)."""

from mde_compass.store.memory import InMemoryStore
from mde_compass.store.migrate import alembic_config, downgrade, upgrade
from mde_compass.store.models import VERSION_TABLE, Base
from mde_compass.store.ports import (
    BriefRepo,
    BriefVersionRecord,
    BriefVersionSummary,
    CallLogEntry,
    CallLogRepo,
    ChangeSetRepo,
    ContributorRepo,
    EventRepo,
    HistoryPage,
    QuestionRecord,
    QuestionRepo,
    SessionRepo,
    SourceRepo,
    Store,
    UnitOfWork,
)
from mde_compass.store.postgres import PgUnitOfWork, PostgresStore, create_store_engine

__all__ = [
    "VERSION_TABLE",
    "Base",
    "BriefRepo",
    "BriefVersionRecord",
    "BriefVersionSummary",
    "CallLogEntry",
    "CallLogRepo",
    "ChangeSetRepo",
    "ContributorRepo",
    "EventRepo",
    "HistoryPage",
    "InMemoryStore",
    "PgUnitOfWork",
    "PostgresStore",
    "QuestionRecord",
    "QuestionRepo",
    "SessionRepo",
    "SourceRepo",
    "Store",
    "UnitOfWork",
    "alembic_config",
    "create_store_engine",
    "downgrade",
    "upgrade",
]
