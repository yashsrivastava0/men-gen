"""PostgreSQL persistence: SQLAlchemy 2.0 async models, repositories implementing the ``Store`` /
``UnitOfWork`` ports from ``mde_contracts``, Alembic migrations, the Postgres job queue."""

from mde_persistence.db import create_engine, normalize_url, redact_url
from mde_persistence.llm_calls import PgLlmCallSink
from mde_persistence.store import PgUnitOfWork, PostgresStore
from mde_persistence.worker import InlineJobRunner, JobHandler, JobWorker

__version__ = "0.1.0"

__all__ = [
    "InlineJobRunner",
    "JobHandler",
    "JobWorker",
    "PgLlmCallSink",
    "PgUnitOfWork",
    "PostgresStore",
    "__version__",
    "create_engine",
    "normalize_url",
    "redact_url",
]
