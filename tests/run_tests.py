#!/usr/bin/env python3
"""Standard library unittest test discovery runner for BenchBenchAgent."""

import os
import sys
import unittest

def main():
    # Ensure current workspace is on sys.path
    workspace_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if workspace_dir not in sys.path:
        sys.path.insert(0, workspace_dir)

    print("=" * 70)
    print(" BenchBenchAgent (BBA) Google ADK 2.6.3 Test Suite Runner")
    print("=" * 70)

    loader = unittest.TestLoader()
    suite = loader.discover(start_dir=os.path.join(workspace_dir, "tests"), pattern="test_*.py")

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    print("=" * 70)
    print(f"Total Tests Run: {result.testsRun}")
    print(f"Errors: {len(result.errors)}")
    print(f"Failures: {len(result.failures)}")
    print(f"Skipped: {len(result.skipped)}")
    print("=" * 70)

    if result.wasSuccessful():
        print("ALL TESTS PASSED SUCCESSFULLY! (100% PASS RATE)")
        sys.exit(0)
    else:
        print("SOME TESTS FAILED.")
        sys.exit(1)


if __name__ == "__main__":
    main()
