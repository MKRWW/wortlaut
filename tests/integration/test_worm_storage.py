"""Integrationstests: WORM-Storage-Adapter gegen echtes MinIO.

Beweist die WORM-Invarianten AC1–AC5, AC7. Object-Lock erzwingt Versionierung;
Legal-Hold schützt die *konkrete Version* — darum prüfen AC2/AC3 die
Unveränderlichkeit auf Versions-Ebene (die gepinnte Version bleibt abrufbar bzw.
kann nicht gelöscht werden), nicht das S3-fremde "zweiter put wirft".
Läuft nur mit dem ``integration``-Marker und Docker (Testcontainers).
"""

import pytest
from minio.error import S3Error

from wortlaut.store.worm import MinioWormStore

pytestmark = pytest.mark.integration


def _version_of(ref: str) -> str:
    return ref.split("?versionId=", 1)[1]


async def test_put_get_roundtrip(worm_store: MinioWormStore) -> None:
    """AC1: put + get liefert exakt dieselben Bytes; Ref ist versions-gepinnt."""
    key = "ac1-key"
    data = b"WORM-roundtrip-bytes"
    ref = await worm_store.put(key, data, content_type="application/octet-stream")
    assert ref.startswith(f"s3://{worm_store._settings.bucket}/{key}?versionId=")  # noqa: SLF001
    got = await worm_store.get(ref)
    assert got == data


async def test_original_version_immutable(worm_store: MinioWormStore) -> None:
    """AC2: Die gepinnte Originalversion bleibt unverändert abrufbar, auch nachdem
    unter demselben key eine neue Version geschrieben wurde (Versions-Immutabilität)."""
    key = "ac2-key"
    original = b"original-bytes"
    other = b"other-bytes"
    ref1 = await worm_store.put(key, original, content_type="application/octet-stream")

    # Ein zweiter put erzeugt eine NEUE Version (S3-Semantik) — die alte bleibt.
    ref2 = await worm_store.put(key, other, content_type="application/octet-stream")
    assert _version_of(ref1) != _version_of(ref2)

    assert await worm_store.get(ref1) == original  # V1 unverändert abrufbar
    assert await worm_store.get(ref2) == other


async def test_locked_version_delete_denied(worm_store: MinioWormStore) -> None:
    """AC3: Die gesperrte Version lässt sich (ohne Bypass) nicht löschen; sie bleibt."""
    key = "ac3-key"
    original = b"delete-proof"
    ref = await worm_store.put(key, original, content_type="application/octet-stream")
    version_id = _version_of(ref)

    client = worm_store._client  # noqa: SLF001
    with pytest.raises(S3Error):
        client.remove_object(
            bucket_name=worm_store._settings.bucket,  # noqa: SLF001
            object_name=key,
            version_id=version_id,
        )

    assert await worm_store.get(ref) == original


async def test_legal_hold_active(worm_store: MinioWormStore) -> None:
    """AC4: Nach put ist Legal-Hold für die gepinnte Version ON."""
    key = "ac4-key"
    ref = await worm_store.put(key, b"hold-data", content_type="application/octet-stream")

    client = worm_store._client  # noqa: SLF001
    assert client.is_object_legal_hold_enabled(
        bucket_name=worm_store._settings.bucket,  # noqa: SLF001
        object_name=key,
        version_id=_version_of(ref),
    )


async def test_ensure_bucket_object_lock_and_idempotent(
    worm_store: MinioWormStore,
) -> None:
    """AC5: Bucket existiert mit Object-Lock; zweiter ensure_bucket ist ok."""
    client = worm_store._client  # noqa: SLF001
    # get_object_lock_config raist, wenn Object-Lock am Bucket NICHT aktiv ist —
    # der erfolgreiche Abruf ist damit selbst der Nachweis der Aktivierung.
    config = client.get_object_lock_config(worm_store._settings.bucket)  # noqa: SLF001
    assert config is not None

    await worm_store.ensure_bucket()


async def test_get_unknown_version_raises(worm_store: MinioWormStore) -> None:
    """AC7: get auf eine unbekannte Version/Key wirft S3Error (kein leerer Erfolg)."""
    bogus = f"s3://{worm_store._settings.bucket}/nonexistent-key?versionId=deadbeefdeadbeef"  # noqa: SLF001
    with pytest.raises(S3Error):
        await worm_store.get(bogus)
