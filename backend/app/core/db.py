"""数据库引擎与会话（SQLAlchemy 2.0）。"""
from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import settings

_connect_args = {"check_same_thread": False} if settings.db_url.startswith("sqlite") else {}
engine = create_engine(settings.db_url, connect_args=_connect_args, echo=False, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _ensure_columns() -> None:
    """SQLite 无迁移框架，create_all 不会给已存在的表补列；新增列在此做幂等 ALTER。"""
    if not settings.db_url.startswith("sqlite"):
        return
    required: dict[str, list[tuple[str, str]]] = {
        "art_styles": [("constraint_manual", "TEXT")],
    }
    with engine.begin() as conn:
        for table, cols in required.items():
            existing = {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})")}
            for name, ddl in cols:
                if name not in existing:
                    conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")


def init_db() -> None:
    # 导入模型以触发注册，再建表
    from app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _ensure_columns()
    from app.services.voice_assignment import seed_preset_voices

    with SessionLocal() as db:
        seed_preset_voices(db)
