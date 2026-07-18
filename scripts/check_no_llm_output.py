#!/usr/bin/env python3
"""wortlaut-Prinzip-Gate: Der Serving-/Output-Layer darf NIE einen LLM-Completion-
Call enthalten. Ausgabe = wörtlicher Span aus der DB, nie modellgenerierter Text.

Sucht Serving-Layer-Dateien und schlägt an, wenn sie verbotene LLM-Call-Muster
enthalten. Existiert noch kein Serving-Layer, passt das Gate (aktiviert sich mit
dem ersten Serving-Code). Details: docs/security.md §3.3, docs/architecture.md §1.

Exit 0 = ok, Exit 1 = Verstoß.
"""

from __future__ import annotations

import pathlib
import re
import sys

# Serving-/Output-Layer: hier darf kein Modell Text erzeugen.
SERVING_GLOBS = ["src/**/serving/**/*.py", "src/**/api/**/*.py", "**/serving/**/*.py"]

# Verbotene Muster (LLM-Textgenerierung im Ausgabepfad).
FORBIDDEN = [
    r"\.chat\.completions\.create",
    r"\.completions\.create",
    r"\.messages\.create",  # Anthropic
    r"\.generate\s*\(",  # generische Generierung
    r"openai\.(ChatCompletion|Completion)",
    r"\bllm\.(generate|complete|chat)\b",
]


def main() -> int:
    root = pathlib.Path(".")
    files: set[pathlib.Path] = set()
    for g in SERVING_GLOBS:
        files.update(root.glob(g))
    if not files:
        print("OK: kein Serving-Layer gefunden — Gate aktiviert sich mit Phase 1.")
        return 0

    violations: list[str] = []
    patterns = [re.compile(p) for p in FORBIDDEN]
    for f in sorted(files):
        for lineno, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            for p in patterns:
                if p.search(line):
                    violations.append(f"{f}:{lineno}: {line.strip()}")

    if violations:
        print("VERSTOSS: LLM-Textgenerierung im Serving-Layer verboten (Prinzip 'kein summarize'):")
        for v in violations:
            print("  " + v)
        return 1

    print(f"OK: {len(files)} Serving-Datei(en) geprüft, kein LLM-Freitext.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
