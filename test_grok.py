"""
Local test script for the Grok/xAI API. Run from project root:

    python test_grok.py

Requires XAI_API_KEY in .env. No Discord or DB needed.

This is a thin wrapper around the pytest live smoke tests.
"""
import subprocess
import sys


def main():
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_grok_live.py", "-v", "--tb=short"],
        check=False,
    )
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
