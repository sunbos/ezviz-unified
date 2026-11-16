"""Record a local SDK diagnostics report with the synchronous client.

Run from the repository root: ``python -m examples.diagnostics_sync``.
Importing this module reads no configuration and performs no network I/O.

The diagnostics report JSON is printed to stdout in ``finally`` so it is
exported even when the call fails; redirect stdout explicitly to save it.
The CLI boundary exits non-zero on failure and prints only a fixed stderr
message that carries no exception details.
"""

import asyncio
import sys

from examples._common import ConfigError, load_config
from ezviz_unified import Client, DiagnosticRecorder, EzvizError

_FAILURE_MESSAGE = "SDK diagnostics example failed; inspect the JSON report."
_INTERRUPTED_MESSAGE = "SDK diagnostics example interrupted; inspect the JSON report."


def main() -> int:
    recorder = DiagnosticRecorder()
    try:
        with recorder.capture():
            config = load_config()
            with Client(
                config.app_key,
                config.app_secret,
                region=config.region_config,
                diagnostic_recorder=recorder,
            ) as client:
                client.devices.list(page_start=0, page_size=1)
    except (ConfigError, EzvizError):
        print(_FAILURE_MESSAGE, file=sys.stderr)
        return 1
    finally:
        print(recorder.to_json())
    return 0


def run_cli() -> int:
    """CLI boundary: collapse unexpected failures to a fixed exit status."""
    try:
        return main()
    except (KeyboardInterrupt, asyncio.CancelledError):
        print(_INTERRUPTED_MESSAGE, file=sys.stderr)
        return 130
    except BaseException:
        print(_FAILURE_MESSAGE, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(run_cli())
