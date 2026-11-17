from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlmodel import Session as DBSession
from sqlmodel import SQLModel, create_engine

from . import models  # noqa: F401  (register tables)
from .config import settings

settings.ensure_dirs()
engine = create_engine(settings.db_url, connect_args={"check_same_thread": False})


def init_db() -> None:
    SQLModel.metadata.create_all(engine)


@contextmanager
def session_scope() -> Iterator[DBSession]:
    with DBSession(engine, expire_on_commit=False) as s:
        yield s
