"""One limit for every real subprocess a test starts or waits on.

These timeouts only stop a hung test; no test asserts that a command is fast.
CI runs pytest with more workers than the runner has cores (PYTEST_WORKERS in
the Makefile), so a stubbed CLI run that takes 3 seconds alone can take five
times as long there. Keep the limit far above any normal run, and change it
here rather than per test. tests/test_hang_guard.py rejects shorter literals.
"""

from __future__ import annotations

SUBPROCESS_TIMEOUT = 120
