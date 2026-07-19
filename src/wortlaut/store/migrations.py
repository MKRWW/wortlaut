"""Programmatischer Alembic-Upgrade, async-sicher.

``upgrade_head`` fährt alle Migrationen bis ``head`` gegen einen DSN. Alembics
Kommando ist synchron und ruft in ``env.py`` selbst ``asyncio.run`` auf — deshalb
läuft es hier in einem Worker-Thread (eigener Event-Loop), unabhängig vom aufrufenden.
"""

import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config

# src/wortlaut/store/migrations.py -> parents[3] == Repo-Root
_REPO_ROOT = Path(__file__).resolve().parents[3]
_MIGRATIONS_DIR = _REPO_ROOT / "migrations"


def _config(dsn: str) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(_MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", dsn)
    return cfg


async def upgrade_head(dsn: str) -> None:
    """Fährt alle Migrationen bis ``head`` gegen ``dsn``."""
    await asyncio.to_thread(command.upgrade, _config(dsn), "head")


async def downgrade_to(dsn: str, revision: str) -> None:
    """Fährt Migrationen bis ``revision`` zurück (Reversibilitätstest, AC9 #2)."""
    await asyncio.to_thread(command.downgrade, _config(dsn), revision)
