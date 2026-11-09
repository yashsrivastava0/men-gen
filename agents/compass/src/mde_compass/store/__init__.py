"""The Compass store: ports (``ports``), an in-memory fake (``memory``) and PostgreSQL (``postgres``),
validated by one shared contract suite (``contract``). Tables are prefixed ``compass_``; the schema is
managed by this package's own Alembic history (``migrate``). The Interview Memory repositories (``qa``,
``facts``, ``summaries``, ``batches``; ADR 0015) never delete."""

from mde_compass.store.memory import InMemoryStore
from mde_compass.store.migrate import alembic_config, downgrade, upgrade
from mde_compass.store.models import VERSION_TABLE, Base
from mde_compass.store.ports import (
    BatchRepo,
    BriefRepo,
    BriefVersionRecord,
    BriefVersionSummary,
    CallLogEntry,
    CallLogRepo,
    ChangeSetRepo,
    ContributorRepo,
    EventRepo,
    FactRepo,
    HistoryPage,
    QaRepo,
    QuestionRecord,
    QuestionRepo,
    SessionRepo,
    SourceRepo,
    Store,
    SummaryRepo,
    UnitOfWork,
)
from mde_compass.store.postgres import PgUnitOfWork, PostgresStore, create_store_engine

__all__ = [
    "VERSION_TABLE",
    "Base",
    "BatchRepo",
    "BriefRepo",
    "BriefVersionRecord",
    "BriefVersionSummary",
    "CallLogEntry",
    "CallLogRepo",
    "ChangeSetRepo",
    "ContributorRepo",
    "EventRepo",
    "FactRepo",
    "HistoryPage",
    "InMemoryStore",
    "PgUnitOfWork",
    "PostgresStore",
    "QaRepo",
    "QuestionRecord",
    "QuestionRepo",
    "SessionRepo",
    "SourceRepo",
    "Store",
    "SummaryRepo",
    "UnitOfWork",
    "alembic_config",
    "create_store_engine",
    "downgrade",
    "upgrade",
]
