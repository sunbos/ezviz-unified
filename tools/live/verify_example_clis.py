"""Bounded local acceptance verifier for the six example CLIs.

Run from a repository checkout::

    python -m tools.live.verify_example_clis --prompt

This is a source-checkout maintenance tool, not SDK runtime. It requires an
explicit ``--prompt`` flag and an interactive TTY, executes each example
``__main__`` in-process via ``runpy`` behind a per-profile transport guard
from ``tools.live._cli_transport``, and prints one redacted JSON receipt to
stdout. Nothing is written to disk and no private value is emitted.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import runpy
import sys

_LIMIT = 65536
_REGION = "domestic"
_CHANNEL = "1"
_DEVICES_SYNC = "examples.devices_sync"
_SCOPE = "domestic_example_clis"
_USAGE = "usage: python -m tools.live.verify_example_clis --prompt"
_BUDGET = "scope: domestic_example_clis; request budget: 20 (8 when the first device page is empty)"
_ERR_CREDS = "error: interactive credentials required"
_ERR_FAILED = "error: verification failed"
_ERR_INTERRUPTED = "error: verification interrupted"
_ENV_KEYS = (
    "EZVIZ_APP_KEY",
    "EZVIZ_APP_SECRET",
    "EZVIZ_REGION",
    "EZVIZ_DEVICE_SERIAL",
    "EZVIZ_CHANNEL",
    "EZVIZ_AUTH_BASE_URL",
)
_VALID_EXITS = frozenset({0, 1, 2, 130})
_MISSING = object()
_INSPECTION_ROWS = (
    ("devices.get", "ok"),
    ("devices.channels", "ok"),
    ("devices.status", "ok"),
    ("diagnostics.capabilities", "ok"),
    ("diagnostics.version", "ok"),
)
_LIST_KEYS = frozenset({"status", "region", "page_start", "page_size", "returned_items"})
# Diagnostic kinds a bounded example run may ever emit; retry/inspection
# events or any other kind corrupt the snapshot.
_DIAG_KINDS = frozenset(
    {
        "application",
        "call",
        "authentication",
        "transport_setup",
        "attempt",
        "close",
        "cleanup",
    }
)
# Terminals every successful diagnostics profile must record exactly once
# beyond its mode-specific close pair.
_DIAG_REQUIRED_TERMINALS = (
    ("application", "application"),
    ("call", "devices.list"),
    ("call", "client.request"),
    ("authentication", "client.request"),
    ("transport_setup", "client.request"),
    ("attempt", "client.request"),
)
_DIAG_CALL_ENTRYPOINTS = frozenset({"devices.list", "client.request"})
_DIAG_INNER_ENTRYPOINTS = frozenset({"client.request"})


class _Cap(io.TextIOBase):
    """In-memory stream that truncates writes past a fixed character budget."""

    encoding = "utf-8"

    def __init__(self, limit: int = _LIMIT) -> None:
        self._limit = limit
        self._parts: list[str] = []
        self._size = 0
        self.truncated = False

    def writable(self) -> bool:
        return True

    def write(self, s) -> int:
        text = str(s)
        if not text:
            return 0
        room = self._limit - self._size
        if room <= 0:
            self.truncated = True
            return len(text)
        if len(text) > room:
            self._parts.append(text[:room])
            self._size += room
            self.truncated = True
        else:
            self._parts.append(text)
            self._size += len(text)
        return len(text)

    def getvalue(self) -> str:
        return "".join(self._parts)


@contextlib.contextmanager
def _env(updates):
    """Set/clear exactly the fixed EZVIZ_* keys, then restore the process env."""
    saved = {key: os.environ.get(key) for key in _ENV_KEYS}
    try:
        for key in _ENV_KEYS:
            value = updates.get(key)
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@contextlib.contextmanager
def _patched(obj, name, value):
    original = getattr(obj, name)
    setattr(obj, name, value)
    try:
        yield
    finally:
        setattr(obj, name, original)


@contextlib.contextmanager
def _watch_page(module, captured):
    """Observe the real print_page_summary call of ``devices_sync`` only.

    Frozen admission instruments that single module; every other module
    runs unwrapped. Only the observed item count and the first usable
    serial are kept in memory; the raw page rows are never retained by
    this runner.
    """
    if module != _DEVICES_SYNC:
        yield
        return
    import examples._common as common

    original = common.print_page_summary

    def wrapper(config_arg, items, *args, **kwargs):
        result = original(config_arg, items, *args, **kwargs)
        captured["calls"] = captured.get("calls", 0) + 1
        if "count" not in captured:
            captured["count"] = len(items) if type(items) is list else None
            captured["serial"] = _serial_from(items)
        return result

    common.print_page_summary = wrapper
    try:
        yield
    finally:
        common.print_page_summary = original


def _invoke(module):
    """Run one example ``__main__`` with captured, capped std streams."""
    prior = sys.modules.pop(module, None)
    stdout, stderr = _Cap(), _Cap()
    old_out, old_err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = stdout, stderr
    code = None
    raised = False
    try:
        runpy.run_module(module, run_name="__main__")
    except SystemExit as exc:
        # Only a genuine SystemExit can carry a valid exit status: a plain
        # runpy return leaves ``code`` None and the profile fails.
        raw = exc.code
        code = 0 if raw is None else (raw if type(raw) is int else None)
    except BaseException:
        raised = True
    finally:
        sys.stdout, sys.stderr = old_out, old_err
        if prior is not None:
            sys.modules[module] = prior
        else:
            sys.modules.pop(module, None)
    return stdout, stderr, code, raised


def _kind(module):
    leaf = module.rsplit(".", 1)[-1]
    if leaf.startswith("inspection"):
        return "inspection"
    if leaf.startswith("diagnostics"):
        return "diagnostics"
    return "list"


def _mode(module):
    return "async" if module.endswith("_async") else "sync"


def _single_json(text):
    try:
        return json.loads(text)
    except ValueError:
        return _MISSING


def _serial_from(items):
    """Bind the target only from a single well-formed first-page row."""
    if not isinstance(items, list) or len(items) != 1:
        return None
    first = items[0]
    if not isinstance(first, dict):
        return None
    serial = first.get("deviceSerial")
    if not isinstance(serial, str) or not serial.strip():
        return None
    if any("a" <= ch <= "z" for ch in serial):
        return None
    return serial


def _list_ok(doc):
    """Exact key set plus strict types: status 'ok' and plain int fields."""
    if not isinstance(doc, dict) or set(doc) != _LIST_KEYS:
        return False
    if type(doc["status"]) is not str or doc["status"] != "ok":
        return False
    if type(doc["region"]) is not str or doc["region"] != _REGION:
        return False
    for key in ("page_start", "page_size", "returned_items"):
        if type(doc[key]) is not int:
            return False
    return doc["page_start"] == 0 and doc["page_size"] == 1 and 0 <= doc["returned_items"] <= 1


def _inspection_ok(doc):
    """Strict summary: exact keys, real ``True``, five exact result rows."""
    if not isinstance(doc, dict) or set(doc) != {"region", "all_succeeded", "results"}:
        return False
    if type(doc["region"]) is not str or doc["region"] != _REGION:
        return False
    if doc["all_succeeded"] is not True:
        return False
    results = doc["results"]
    if type(results) is not list or len(results) != len(_INSPECTION_ROWS):
        return False
    for row, (operation, status) in zip(results, _INSPECTION_ROWS):
        if not isinstance(row, dict) or set(row) != {"operation", "status"}:
            return False
        if type(row["operation"]) is not str or row["operation"] != operation:
            return False
        if type(row["status"]) is not str or row["status"] != status:
            return False
    return True


def _event_entrypoint_ok(kind, entrypoint, close_entry):
    """Whether ``entrypoint`` is the only allowed value for ``kind``."""
    if kind == "application":
        return entrypoint == "application"
    if kind == "call":
        return entrypoint in _DIAG_CALL_ENTRYPOINTS
    if kind in ("authentication", "transport_setup", "attempt"):
        return entrypoint in _DIAG_INNER_ENTRYPOINTS
    if kind == "close":
        return entrypoint == close_entry
    return entrypoint == "client.cleanup"


def _diagnostics_ok(doc, recorder, mode):
    """Compare stdout to the live recorder snapshot and validate every row.

    ``passed`` requires exact equality with ``recorder.report()``, the
    exact-int schema ``1``/zero counters, the fixed scope, whitelisted
    kinds/entrypoints, the profile mode on every non-application event
    (application rows carry ``mode == "application"``), and exactly one
    ``succeeded`` terminal for each required stage — application capture,
    the ``devices.list`` call, its inner ``client.request`` call, the
    authentication/transport_setup/attempt scopes, and the mode's close
    (``client.close``/``client.aclose``; an ``client.cleanup`` terminal is
    allowed once for async). Any ``failed``/``cancelled``/``interrupted``/
    ``scheduled``/retry row or extra terminal fails the profile.
    """
    if not isinstance(doc, dict) or recorder is None:
        return False
    try:
        expected = recorder.report()
    except Exception:
        return False
    if doc != expected:
        return False
    if (
        type(doc.get("schema_version")) is not int
        or doc["schema_version"] != 1
        or type(doc.get("scope")) is not str
        or doc["scope"] != "local_sdk_diagnostics"
        or type(doc.get("max_events")) is not int
        or type(doc.get("dropped_events")) is not int
        or doc["dropped_events"] != 0
        or type(doc.get("recording_errors")) is not int
        or doc["recording_errors"] != 0
    ):
        return False
    events = doc.get("events")
    if not isinstance(events, list) or not events:
        return False
    close_entry = "client.aclose" if mode == "async" else "client.close"
    terminals: list[tuple[str, str]] = []
    for event in events:
        if not isinstance(event, dict):
            return False
        kind = event.get("kind")
        entrypoint = event.get("entrypoint")
        outcome = event.get("outcome")
        if not isinstance(kind, str) or kind not in _DIAG_KINDS:
            return False
        if kind == "cleanup" and mode != "async":
            return False
        if type(entrypoint) is not str or not _event_entrypoint_ok(kind, entrypoint, close_entry):
            return False
        expected_mode = "application" if kind == "application" else mode
        if event.get("mode") != expected_mode:
            return False
        if outcome == "started":
            continue
        if outcome != "succeeded":
            return False
        terminals.append((kind, entrypoint))
    required = [*_DIAG_REQUIRED_TERMINALS, ("close", close_entry)]
    for key in required:
        if terminals.count(key) != 1:
            return False
    extras = [key for key in terminals if key not in required]
    if any(key != ("cleanup", "client.cleanup") for key in extras):
        return False
    return extras.count(("cleanup", "client.cleanup")) <= 1


def _doc_ok(kind, doc, mode, recorder):
    if kind == "inspection":
        return _inspection_ok(doc)
    if kind == "diagnostics":
        return _diagnostics_ok(doc, recorder, mode)
    return _list_ok(doc)


def _skip_row(module, result):
    return {
        "module": module,
        "result": result,
        "http_requests": 0,
        "exit_code": None,
        "boundary_rejected": False,
    }


def _run_profile(module, config, serial, guard, cli):
    """Execute one example ``__main__`` behind its transport guard.

    ``guard`` is created by ``verify`` before this call so a runner-side
    failure can never hide HTTP requests the profile already admitted.
    Returns ``(row, ok, serial)`` where ``serial`` is the validated
    first-page device serial observed by the page watcher (``None`` for
    every module but ``devices_sync`` or an unusable page).
    """
    import ezviz_unified

    attr = "AsyncClient" if _mode(module) == "async" else "Client"
    original = getattr(ezviz_unified, attr)
    captured: dict = {}

    def factory(*args, **kwargs):
        recorder = kwargs.get("diagnostic_recorder")
        if recorder is not None:
            captured["recorder"] = recorder
        merged = dict(kwargs)
        merged.update(guard.client_kwargs())
        return original(*args, **merged)

    updates = {
        "EZVIZ_APP_KEY": config["app_key"],
        "EZVIZ_APP_SECRET": config["app_secret"],
        "EZVIZ_REGION": _REGION,
    }
    if serial is not None:
        updates["EZVIZ_DEVICE_SERIAL"] = serial
        updates["EZVIZ_CHANNEL"] = _CHANNEL

    with _env(updates), _patched(ezviz_unified, attr, factory), _watch_page(module, captured):
        stdout, stderr, code, raised = _invoke(module)

    ok = (
        not raised
        and code == 0
        and not stdout.truncated
        and not stderr.truncated
        and stderr.getvalue() == ""
        and not guard.rejected
        and guard.complete
    )
    doc = _MISSING
    if ok:
        doc = _single_json(stdout.getvalue())
        ok = doc is not _MISSING and _doc_ok(
            _kind(module), doc, _mode(module), captured.get("recorder")
        )
    found = None
    if module == _DEVICES_SYNC:
        calls = captured.get("calls", 0)
        count = captured.get("count")
        # The real helper must have run exactly once and its observed item
        # count must equal the decoded stdout; a plausible document without
        # the helper call still fails.
        if calls != 1 or type(count) is not int:
            ok = False
        elif doc is _MISSING or doc.get("returned_items") != count:
            ok = False
        elif ok:
            found = captured.get("serial")
    row = {
        "module": module,
        "result": "passed" if ok else "failed",
        "http_requests": len(guard.labels),
        "exit_code": code if code in _VALID_EXITS else None,
        "boundary_rejected": guard.rejected,
    }
    return row, ok, found


def verify(config: dict[str, str]) -> dict:
    """Run all six profiles in order and return the redacted receipt mapping.

    Only ``config["app_key"]`` and ``config["app_secret"]`` are consumed.
    """
    from tools.live import _cli_transport as cli

    rows = []
    serial = None
    failed = False
    for module in cli.MODULES:
        if failed:
            rows.append(_skip_row(module, "skipped_after_failure"))
            continue
        if _kind(module) == "inspection" and serial is None:
            rows.append(_skip_row(module, "skipped_no_target"))
            continue
        target = serial if _kind(module) == "inspection" else None
        guard = cli.ProfileGuard(module, target)
        try:
            row, ok, found = _run_profile(module, config, target, guard, cli)
        except Exception:
            # A runner-side failure must still report the guard's real
            # admission trace — never a fabricated zero.
            row = {
                "module": module,
                "result": "failed",
                "http_requests": len(guard.labels),
                "exit_code": None,
                "boundary_rejected": guard.rejected,
            }
            ok, found = False, None
        rows.append(row)
        if not ok:
            failed = True
        elif module == _DEVICES_SYNC:
            serial = found
    if all(row["result"] == "passed" for row in rows):
        outcome = "passed"
    elif any(row["result"] == "failed" for row in rows):
        outcome = "failed"
    else:
        outcome = "not_verified"
    return {
        "schema_version": 1,
        "scope": _SCOPE,
        "outcome": outcome,
        "http_requests": sum(row["http_requests"] for row in rows),
        "profiles": rows,
    }


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else list(argv)
    if args == ["--help"]:
        print(_USAGE)
        return 0
    if args != ["--prompt"]:
        print(_USAGE, file=sys.stderr)
        return 2
    print(_BUDGET, file=sys.stderr)
    try:
        from tools.live.verify_diagnostics_async import _read_credentials

        config = _read_credentials()
    except KeyboardInterrupt:
        print(_ERR_INTERRUPTED, file=sys.stderr)
        return 130
    except Exception:
        print(_ERR_CREDS, file=sys.stderr)
        return 2
    try:
        receipt = verify(config)
    except KeyboardInterrupt:
        print(_ERR_INTERRUPTED, file=sys.stderr)
        return 130
    except Exception:
        print(_ERR_FAILED, file=sys.stderr)
        return 1
    print(json.dumps(receipt))
    return 0 if receipt["outcome"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
