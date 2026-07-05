"""Run ThinkFlow tests without external test dependencies."""

import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, os.path.dirname(__file__))

import test_core
import test_parser


def main():
    test_parser.run_all()
    test_core.run_all()
    print("全部测试通过 ✓")


if __name__ == "__main__":
    main()
