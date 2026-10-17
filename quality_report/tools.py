from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def warn(message: str) -> None:
    print(f"quality-report: warning: {message}", file=sys.stderr)


def run_tool(
    args: list[str], *, cwd: Path | None = None, timeout: int = 600
) -> subprocess.CompletedProcess[str] | None:
    """Run an external tool; None (with a warning) if it is missing, unrunnable or times out.

    The exit code is left to the caller: some tools exit non-zero on findings.
    """
    name = args[0]
    try:
        return subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False)
    except FileNotFoundError:
        warn(f"{name} not found; skipped")
    except subprocess.TimeoutExpired:
        warn(f"{name} timed out after {timeout}s; skipped")
    except OSError as exc:
        warn(f"{name} could not run ({exc}); skipped")
    return None
