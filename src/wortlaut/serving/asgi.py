"""ASGI-Composition-Root: ENV -> Engine -> Sessionmaker -> MinioWormStore -> ``create_app``.

Factory statt Modul-``app`` (Spec 0081 §4.2): ``create_asgi_app`` liest beim Aufruf
— nie beim Import — die Settings und baut die Engine. Ein Modul-Level-``app =
create_asgi_app()`` würde jeden Import (Test, ``--help``, Tooling) zu voller ENV
zwingen und einen Konfigurationsfehler als Import-Traceback statt als saubere
Exit-2-Meldung (``wortlaut.cli._run_serve``) produzieren.

Je uvicorn-Worker-Prozess wird die Factory genau einmal aufgerufen und baut damit
eine eigene Engine samt eigenem asyncpg-Pool (Spec 0081 §4.1): ein Pool darf
nicht über ``fork`` geteilt werden. ``python -m wortlaut serve`` übergibt uvicorn
deshalb den Import-String ``"wortlaut.serving.asgi:create_asgi_app"`` mit
``factory=True`` — nie eine App-Instanz (sonst würde uvicorn ``workers`` still
ignorieren, §0b).
"""

from fastapi import FastAPI

from wortlaut.serving.app import create_app
from wortlaut.store.db import create_async_engine_from, make_sessionmaker
from wortlaut.store.settings import DbSettings, WormSettings
from wortlaut.store.worm import MinioWormStore


def create_asgi_app() -> FastAPI:
    engine = create_async_engine_from(DbSettings())
    return create_app(make_sessionmaker(engine), MinioWormStore(WormSettings()))
