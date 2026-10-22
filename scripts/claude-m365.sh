#!/usr/bin/env bash
# Launch Claude Code against the local M365 gateway.
# Nothing here rewrites config files or starts the proxy: it only sets the five
# env vars the CLI reads and execs it with every argument forwarded.
set -euo pipefail

export ANTHROPIC_BASE_URL="${ANTHROPIC_BASE_URL:-http://127.0.0.1:4142}"
export ANTHROPIC_AUTH_TOKEN="${ANTHROPIC_AUTH_TOKEN:-dummy}"
export ANTHROPIC_MODEL="${ANTHROPIC_MODEL:-claude-sonnet-4-5}"
export CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1

claude_bin="${CLAUDE_BIN:-}"
if [ -z "$claude_bin" ]; then
  if command -v claude >/dev/null 2>&1; then
    claude_bin=claude
  elif [ -x "$HOME/.local/bin/claude" ]; then
    claude_bin="$HOME/.local/bin/claude"
  else
    echo "claude not found: set CLAUDE_BIN to the CLI binary" >&2
    exit 127
  fi
fi

exec "$claude_bin" "$@"
