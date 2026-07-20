"""Unit Layering: ingest importiert NICHT aus dem Kern (AC6 / R-ARCH-02).

Scannt alle .py-Dateien unter src/wortlaut/ingest und stellt sicher,
dass keiner der core-Module importiert wird.
"""

from __future__ import annotations

import ast
from pathlib import Path

FORBIDDEN_IMPORTS = {
    "wortlaut.evidence",
    "wortlaut.store",
    "wortlaut.archive",
    "wortlaut.pipeline",
}

INGEST_ROOT = Path(__file__).resolve().parent.parent.parent / "src" / "wortlaut" / "ingest"


def test_ingest_does_not_import_core() -> None:
    """Statisch prüfen: kein ingest-Modul importiert aus evidence/store/archive/pipeline."""
    violations: list[str] = []

    for py_file in INGEST_ROOT.rglob("*.py"):
        source = py_file.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source, filename=str(py_file))
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    _check_import(alias.name, str(py_file), violations)
            elif isinstance(node, ast.ImportFrom):
                if node.module is not None:
                    _check_import(node.module, str(py_file), violations)

    assert not violations, (
        "AC6 / R-ARCH-02: ingest-Paket darf core-Module nicht importieren:\n"
        + "\n".join(f"  {v}" for v in violations)
    )


def _check_import(module: str, filename: str, violations: list[str]) -> None:
    """Prüft, ob ein Modulname unter den verbotenen Importen liegt."""
    for forbidden in FORBIDDEN_IMPORTS:
        if module == forbidden or module.startswith(forbidden + "."):
            violations.append(f"{filename} → imports '{module}' (forbidden: {forbidden})")
