"""Eintrittspunkt fuer ``python -m wortlaut``."""

import sys

from wortlaut.cli import main

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
