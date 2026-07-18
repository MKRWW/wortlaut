"""Smoke-Tests fürs Grundgerüst: Paket + Layer-Pakete importierbar."""

import wortlaut


def test_version() -> None:
    assert wortlaut.__version__ == "0.0.0"


def test_layers_import() -> None:
    from wortlaut import evidence, ingest, retrieval, serving, store

    for mod in (ingest, evidence, store, retrieval, serving):
        assert mod.__name__.startswith("wortlaut.")
