#!/usr/bin/env python3
"""Run the whole suite; the build scripts call this before packaging."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest


TESTS = Path(__file__).resolve().parent


def main() -> int:
    sys.path.insert(0, str(TESTS))
    suite = unittest.defaultTestLoader.discover(str(TESTS))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
