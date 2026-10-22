#!/usr/bin/env pwsh
# Launch Claude Code against the local M365 gateway.
# Nothing here rewrites config files or starts the proxy: it only sets the four
# env vars the CLI reads and execs it with every argument forwarded.
#
# No [CmdletBinding()] and no param() block on purpose: with them, PowerShell
# binds `-p` to the common `-PipelineVariable` parameter, swallows the prompt and
# runs `claude` with no arguments. `$args` forwards everything verbatim.
$ErrorActionPreference = "Stop"

if (-not $env:ANTHROPIC_BASE_URL) { $env:ANTHROPIC_BASE_URL = "http://127.0.0.1:4142" }
if (-not $env:ANTHROPIC_AUTH_TOKEN) { $env:ANTHROPIC_AUTH_TOKEN = "dummy" }
if (-not $env:ANTHROPIC_MODEL) { $env:ANTHROPIC_MODEL = "claude-sonnet-4-5" }
$env:CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC = "1"

$claude = if ($env:CLAUDE_BIN) { $env:CLAUDE_BIN } else { "claude" }
if (-not (Get-Command $claude -ErrorAction SilentlyContinue)) {
    $fallback = Join-Path $HOME ".local/bin/claude.exe"
    if (Test-Path $fallback) { $claude = $fallback } else {
        throw "claude not found: set CLAUDE_BIN to the CLI binary"
    }
}

& $claude @args
exit $LASTEXITCODE
