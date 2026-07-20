"""Unit-Tests: WORM-Adapter API-Oberfläche und Settings.

Prüft AC6 (kein delete/remove/release_hold/overwrite in der öffentlichen API)
und dass WormSettings Umgebungsvariablen korrekt liest.
"""

import inspect

import pytest

from wortlaut.store.settings import WormSettings
from wortlaut.store.worm import MinioWormStore


def test_no_delete_or_release_in_public_api() -> None:
    """AC6: Die öffentliche API enthält nur ensure_bucket, put, get."""
    forbidden = {"delete", "remove", "release_hold", "overwrite"}
    public = {
        name
        for name, _ in inspect.getmembers(MinioWormStore, predicate=inspect.isfunction)
        if not name.startswith("_")
    }
    overlap = public & forbidden
    assert not overlap, f"Unerwartete öffentliche Methoden gefunden: {overlap}"
    expected = {"ensure_bucket", "put", "get"}
    assert public == expected, f"Erwartet {expected}, erhalten {public}"


@pytest.mark.parametrize(
    ("env_vars", "expected"),
    [
        (
            {
                "WORTLAUT_WORM_ENDPOINT": "minio.local:9000",
                "WORTLAUT_WORM_ACCESS_KEY": "minioadmin",
                "WORTLAUT_WORM_SECRET_KEY": "minio123",
            },
            {
                "endpoint": "minio.local:9000",
                "access_key": "minioadmin",
                "secret_key": "minio123",
                "bucket": "wortlaut-worm",
                "secure": True,
            },
        ),
        (
            {
                "WORTLAUT_WORM_ENDPOINT": "s3.example.com",
                "WORTLAUT_WORM_ACCESS_KEY": "ak",
                "WORTLAUT_WORM_SECRET_KEY": "sk",
                "WORTLAUT_WORM_BUCKET": "custom-bucket",
                "WORTLAUT_WORM_SECURE": "false",
            },
            {
                "endpoint": "s3.example.com",
                "access_key": "ak",
                "secret_key": "sk",
                "bucket": "custom-bucket",
                "secure": False,
            },
        ),
    ],
)
def test_worm_settings_from_env(
    monkeypatch: pytest.MonkeyPatch,
    env_vars: dict[str, str],
    expected: dict[str, object],
) -> None:
    for key, value in env_vars.items():
        monkeypatch.setenv(key, value)
    settings = WormSettings()
    for field, expected_value in expected.items():
        actual = getattr(settings, field)
        assert actual == expected_value, f"Field {field}: expected {expected_value!r}, got {actual!r}"
