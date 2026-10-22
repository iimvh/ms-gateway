"""The launchers must forward the CLI arguments verbatim.

The Windows launcher used `[CmdletBinding()]` + `ValueFromRemainingArguments`,
which makes PowerShell bind `-p` to the common `-PipelineVariable` parameter and
swallow the prompt — the README's own quick start then ran `claude` with no args.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def _find_powershell() -> str | None:
    """`pwsh` when present, else Windows PowerShell — the CmdletBinding trap is in both."""
    for candidate in ("pwsh", "powershell"):
        found = shutil.which(candidate)
        if found:
            return found
    for absolute in (
        r"C:\Program Files\PowerShell\7\pwsh.exe",
        r"C:\WINDOWS\System32\WindowsPowerShell\v1.0\powershell.exe",
    ):
        if Path(absolute).exists():
            return absolute
    return None


PWSH = _find_powershell()


def _fake_claude(path: Path) -> Path:
    """A .cmd shim that appends every forwarded argument to CLAUDE_ARGS_FILE."""
    shim = path / "fake-claude.cmd"
    shim.write_text(
        "@echo off\r\n"
        ":loop\r\n"
        'if "%~1"=="" goto end\r\n'
        'echo %~1>> "%CLAUDE_ARGS_FILE%"\r\n'
        "shift\r\n"
        "goto loop\r\n"
        ":end\r\n",
        encoding="utf-8",
    )
    return shim


@pytest.mark.skipif(PWSH is None, reason="PowerShell 7 (pwsh) is not available")
def test_powershell_launcher_forwards_the_prompt_flag(tmp_path):
    shim = _fake_claude(tmp_path)
    args_file = tmp_path / "args.txt"
    env = {
        **os.environ,
        "CLAUDE_BIN": str(shim),
        "CLAUDE_ARGS_FILE": str(args_file),
        "ANTHROPIC_BASE_URL": "http://127.0.0.1:4142",
    }
    result = subprocess.run(
        [PWSH, "-NoProfile", "-File", str(REPO / "scripts" / "claude-m365.ps1"), "-p", "hello"],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    forwarded = args_file.read_text(encoding="utf-8").split() if args_file.exists() else []
    assert forwarded == ["-p", "hello"]
