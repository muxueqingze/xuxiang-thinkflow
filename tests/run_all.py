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
    for pattern in ('test_cli_persistence.py', 'test_process_contracts.py', 'test_python_runtime.py',
                    'test_message_projection.py', 'test_dispatch_contracts.py',
                    'test_anthropic_roundtrip.py', 'test_session_incremental.py'):
        suite.addTests(unittest.defaultTestLoader.discover(os.path.dirname(__file__), pattern=pattern))
    suite.addTests(unittest.defaultTestLoader.discover(os.path.dirname(__file__), pattern="test_desktop_catalog.py"))
    suite.addTests(unittest.defaultTestLoader.discover(os.path.dirname(__file__), pattern="test_cli_interaction.py"))
    suite.addTests(unittest.defaultTestLoader.discover(os.path.dirname(__file__), pattern="test_desktop_services.py"))
    suite.addTests(unittest.defaultTestLoader.discover(os.path.dirname(__file__), pattern="test_conversation_context.py"))
    suite.addTests(unittest.defaultTestLoader.discover(os.path.dirname(__file__), pattern="test_output_recall.py"))
    suite.addTests(unittest.defaultTestLoader.discover(os.path.dirname(__file__), pattern="test_read_ranges.py"))
    suite.addTests(unittest.defaultTestLoader.discover(os.path.dirname(__file__), pattern="test_configuration.py"))
    suite.addTests(unittest.defaultTestLoader.discover(os.path.dirname(__file__), pattern="test_context_budget.py"))
    suite.addTests(unittest.defaultTestLoader.discover(os.path.dirname(__file__), pattern="test_optional_output_budget.py"))
    suite.addTests(unittest.defaultTestLoader.discover(os.path.dirname(__file__), pattern="test_efficiency_runtime.py"))
    suite.addTests(unittest.defaultTestLoader.discover(os.path.dirname(__file__), pattern="test_prompt_layers.py"))
    suite.addTests(unittest.defaultTestLoader.discover(os.path.dirname(__file__), pattern="test_efficiency_benchmark.py"))
    for pattern in ("test_harness.py", "test_desktop_service.py", "test_desktop_navigation.py", "test_security_paths.py", "test_runtime_metadata.py", "test_command_inbox.py", "test_file_revisions.py", "test_workspace_protocol.py", "test_v08_round2_core.py", "test_v08_cross_review.py", "test_native_write_fallback.py", "test_markdown_payload.py", "test_bounded_bash.py", "test_attribute_encoding.py", "test_benchmark_tasks.py", "test_benchmark_meter.py", "test_benchmark_recovery.py", "test_plan_receipts.py", "test_benchmark_monitor.py", "test_parser_diagnostics.py"):
        suite.addTests(unittest.defaultTestLoader.discover(os.path.dirname(__file__), pattern=pattern))
    if not unittest.TextTestRunner(verbosity=1).run(suite).wasSuccessful():
        raise SystemExit(1)
    print("全部测试通过 ✓")


if __name__ == "__main__":
    main()
