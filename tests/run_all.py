"""Run ThinkFlow tests without external test dependencies."""

import os
import sys
import unittest

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, os.path.dirname(__file__))

import test_core
import test_parser


def main():
    test_parser.run_all()
    test_core.run_all()
    suite = unittest.TestSuite()
    for pattern in ("test_harness.py", "test_desktop_service.py", "test_desktop_navigation.py", "test_security_paths.py", "test_runtime_metadata.py"):
        suite.addTests(unittest.defaultTestLoader.discover(os.path.dirname(__file__), pattern=pattern))
    if not unittest.TextTestRunner(verbosity=1).run(suite).wasSuccessful():
        raise SystemExit(1)
    print("全部测试通过 ✓")


if __name__ == "__main__":
    main()
