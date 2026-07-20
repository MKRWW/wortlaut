"""WORM-Storage-Adapter: unveränderlicher Beweis-Speicher für Rohbytes.

MinIO S3 Object-Lock (Governance-Mode + Legal-Hold) als append-only Storage.
Sync-SDK in asyncio.to_thread gekapselt — async Surface, kein aioboto3.

Object-Lock erzwingt Versionierung: Legal-Hold schützt die *konkrete Version*
vor Löschung/Änderung, nicht den Namen vor neuen Versionen/Delete-Markern. Darum
pinnt ``put`` die version-id in die zurückgegebene Ref, und ``get`` liest exakt
diese Version — so ist die Originalversion immer nachweisbar unverändert abrufbar
(Grundlage für #8 /verify). R-DATA-01, R-SEC-01, ADR-0007.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from io import BytesIO
from typing import Protocol

from minio import Minio

from wortlaut.store.settings import WormSettings

_REF_SCHEME = "s3://"
_VERSION_SEP = "?versionId="


@dataclass(frozen=True)
class _ObjectRef:
    bucket: str
    key: str
    version_id: str


def _format_ref(bucket: str, key: str, version_id: str) -> str:
    return f"{_REF_SCHEME}{bucket}/{key}{_VERSION_SEP}{version_id}"


def _parse_ref(ref: str) -> _ObjectRef:
    if not ref.startswith(_REF_SCHEME) or _VERSION_SEP not in ref:
        raise ValueError(f"kein gültiger WORM-Ref: {ref!r}")
    body, version_id = ref[len(_REF_SCHEME) :].split(_VERSION_SEP, 1)
    bucket, _, key = body.partition("/")
    if not bucket or not key or not version_id:
        raise ValueError(f"kein gültiger WORM-Ref: {ref!r}")
    return _ObjectRef(bucket=bucket, key=key, version_id=version_id)


class WormStore(Protocol):
    """Öffentliche Schnittstelle für WORM-Objektspeicher.

    Nur ``ensure_bucket``, ``put``, ``get`` — kein Delete, kein Overwrite,
    kein Legal-Hold-Release (R-DATA-01).
    """

    async def ensure_bucket(self) -> None: ...

    async def put(self, key: str, data: bytes, *, content_type: str) -> str: ...

    async def get(self, ref: str) -> bytes: ...


class MinioWormStore:
    """MinIO-basierter WORM-Speicher mit Object-Lock und Legal-Hold.

    Jede gespeicherte Version erhält Legal-Hold=ON und kann weder überschrieben
    noch gelöscht werden (Governance-Mode). ``put`` gibt eine versions-gepinnte
    Ref zurück, ``get`` liest exakt diese Version.
    """

    def __init__(self, settings: WormSettings) -> None:
        self._settings = settings
        self._client = Minio(
            endpoint=settings.endpoint,
            access_key=settings.access_key,
            secret_key=settings.secret_key,
            secure=settings.secure,
        )

    async def ensure_bucket(self) -> None:
        """Erstellt den Bucket mit aktiviertem Object-Lock (idempotent).

        Existiert der Bucket bereits, wird nichts unternommen und kein Fehler
        geworfen. Object-Lock ist nur bei Bucket-Creation setzbar.
        """

        def _ensure() -> None:
            if self._client.bucket_exists(self._settings.bucket):
                return
            self._client.make_bucket(self._settings.bucket, object_lock=True)

        await asyncio.to_thread(_ensure)

    async def put(self, key: str, data: bytes, *, content_type: str) -> str:
        """Legt eine neue, per Legal-Hold gesperrte Version des Objekts ab.

        Gibt eine versions-gepinnte, stabile Referenz im Format
        ``s3://{bucket}/{key}?versionId={version_id}`` zurück, die später in
        ``source.raw_bytes_ref`` landet und die exakte Version identifiziert.
        """

        def _put() -> str:
            bucket = self._settings.bucket
            result = self._client.put_object(
                bucket_name=bucket,
                object_name=key,
                data=BytesIO(data),
                length=len(data),
                content_type=content_type,
                legal_hold=True,
            )
            if result.version_id is None:  # pragma: no cover — Object-Lock erzwingt Versionierung
                raise RuntimeError("Bucket ohne Versionierung — Object-Lock nicht aktiv")
            return _format_ref(bucket, key, result.version_id)

        return await asyncio.to_thread(_put)

    async def get(self, ref: str) -> bytes:
        """Liest exakt die in *ref* gepinnte Version und gibt die Rohbytes zurück.

        Fehlt das Objekt/die Version, wird die zugrundeliegende S3Error propagiert
        — niemals leere Bytes oder None.
        """
        parsed = _parse_ref(ref)

        def _get() -> bytes:
            response = self._client.get_object(
                bucket_name=parsed.bucket,
                object_name=parsed.key,
                version_id=parsed.version_id,
            )
            try:
                return response.read()
            finally:
                response.close()
                response.release_conn()

        return await asyncio.to_thread(_get)
