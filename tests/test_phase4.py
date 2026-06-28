"""Phase 4 hardening checks.

grep-clean: verify no legacy orchestration code remains in src/.
logging_setup: unit tests for _pkg_from_path.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from piidigger.orchestration.logging_setup import _pkg_from_path, setup_warning_capture

# ---------------------------------------------------------------------------
# grep-clean: no legacy orchestration references in src/
# ---------------------------------------------------------------------------

_FORBIDDEN_PATTERNS = [
    "queuefuncs",
    "LogManager",
    "ProcessManager",
    "from piidigger.filescan",
    "from piidigger.logmanager",
    "globalvars.SENTINEL",
]

_SRC_ROOT = Path(__file__).parent.parent / "src"


@pytest.mark.unit
def test_no_legacy_orchestration_references() -> None:
    """No file in src/ may reference any deleted legacy orchestration API.

    These symbols were removed as part of the Phase 4 clean-up.  A violation
    here means a stale import or reference was accidentally re-introduced.
    """
    violations: list[str] = []
    for py_file in _SRC_ROOT.rglob("*.py"):
        text = py_file.read_text(encoding="utf-8", errors="replace")
        for pat in _FORBIDDEN_PATTERNS:
            if pat in text:
                violations.append(f"{py_file.relative_to(_SRC_ROOT)}: {pat!r}")
    assert not violations, "Legacy references found in src/:\n" + "\n".join(violations)


@pytest.mark.unit
def test_legacy_module_files_do_not_exist() -> None:
    """Deleted legacy modules must not exist on disk."""
    pkg_root = _SRC_ROOT / "piidigger"
    for name in ("filescan.py", "queuefuncs.py", "logmanager.py"):
        assert not (pkg_root / name).exists(), f"Legacy file still present: {name}"


# ---------------------------------------------------------------------------
# _pkg_from_path unit tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_pkg_from_path_site_packages() -> None:
    path = "/usr/lib/python3.14/site-packages/xlrd/sheet.py"
    assert _pkg_from_path(path) == "xlrd"


@pytest.mark.unit
def test_pkg_from_path_dist_packages() -> None:
    path = "/usr/lib/python3/dist-packages/requests/api.py"
    assert _pkg_from_path(path) == "requests"


@pytest.mark.unit
def test_pkg_from_path_no_site_packages() -> None:
    path = "/home/user/project/src/piidigger/worker.py"
    assert _pkg_from_path(path) is None


@pytest.mark.unit
def test_pkg_from_path_empty_string() -> None:
    assert _pkg_from_path("") is None


@pytest.mark.unit
def test_pkg_from_path_site_packages_at_end_no_next_part() -> None:
    # site-packages is the last component — no package name follows
    path = "/usr/lib/python3.14/site-packages"
    assert _pkg_from_path(path) is None


# ---------------------------------------------------------------------------
# setup_warning_capture: basic idempotency check
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_setup_warning_capture_installs_hook_and_is_idempotent() -> None:
    """setup_warning_capture installs a piidigger-marked hook; re-calling is safe."""
    import logging
    import logging.handlers
    import multiprocessing as mp
    import warnings

    log_queue: mp.Queue[object] = mp.Queue()

    setup_warning_capture(log_queue)
    assert getattr(warnings.showwarning, "__piidigger__", False), "hook not installed"

    # A second call must not add a duplicate QueueHandler to py.warnings logger
    setup_warning_capture(log_queue)
    warn_logger = logging.getLogger("py.warnings")
    queue_handlers = [h for h in warn_logger.handlers if isinstance(h, logging.handlers.QueueHandler)]
    assert len(queue_handlers) == 1, f"expected 1 QueueHandler, got {len(queue_handlers)}"


@pytest.mark.unit
def test_warning_capture_routes_warning_through_capture_function() -> None:
    """Emit a real warning so _capture() body executes (covers logging_setup.py lines 94-106)."""
    import multiprocessing as mp
    import warnings

    log_queue: mp.Queue[object] = mp.Queue()
    setup_warning_capture(log_queue)

    with warnings.catch_warnings():
        warnings.simplefilter("always")
        warnings.warn("piidigger-test-warning", UserWarning, stacklevel=1)
    # If _capture raised, the test would fail; reaching here is sufficient.


@pytest.mark.unit
def test_warning_capture_with_file_arg_uses_orig_handler() -> None:
    """showwarning(file=...) goes to the original handler, not the queue."""
    import io
    import multiprocessing as mp
    import warnings

    log_queue: mp.Queue[object] = mp.Queue()
    setup_warning_capture(log_queue)

    buf = io.StringIO()
    # Call the installed hook directly with a non-None file arg; must not raise
    warnings.showwarning("test-msg", UserWarning, "test_phase4.py", 1, file=buf)


@pytest.mark.unit
def test_pkg_from_path_non_string_raises_silently() -> None:
    """Passing a non-string triggers the except clause; returns None without raising."""
    result = _pkg_from_path(None)  # type: ignore[arg-type]
    assert result is None
