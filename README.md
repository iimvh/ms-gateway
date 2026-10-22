# Claude Code ⇄ M365 Copilot

Point the real `claude` CLI at Microsoft 365 Copilot by translating the Anthropic
Messages API into the OpenAI-compatible API of
[`m365-copilot-proxy`](../m365-copilot-proxy) (the Node repo, unchanged, and still
the only M365 client).

```
claude (Claude Code CLI)
   │  Anthropic Messages API, stream:true, tools…
   ▼
gateway (FastAPI, 127.0.0.1:4142)      ← this repo
   │  OpenAI chat.completions, tools, stream:false
   ▼
m365-proxy (Node, 127.0.0.1:4141)
   │  SignalR / WebSocket
   ▼
M365 Copilot
```

The gateway is **only a model endpoint**. Claude Code keeps its own tools,
permission prompts, subagents (`Task`), MCP servers, plan mode, `/context` and
compaction; every one of those is just more requests here.

Design: [`docs/superpowers/specs/2026-10-22-claude-code-anthropic-gateway-design.md`](docs/superpowers/specs/2026-10-22-claude-code-anthropic-gateway-design.md).
Plan: [`docs/superpowers/plans/2026-10-22-claude-code-anthropic-gateway.md`](docs/superpowers/plans/2026-10-22-claude-code-anthropic-gateway.md).

## Quick start

```sh
# 1 — the Node proxy (in m365-copilot-proxy), against a signed-in M365 account
pnpm --filter @m365-copilot/proxy dev                      # :4141
# M365_AVOID_THROTTLING=1 is recommended for a long Claude Code loop

# 2 — the gateway (this repo)
uv sync
uv run uvicorn gateway.main:app --port 4142

# 3 — Claude Code, through the launcher (sets the env for you)
scripts/claude-m365.ps1 -p "create hello.py that prints hi, run it"
scripts/claude-m365.sh -p "create hello.py that prints hi, run it"   # POSIX
```

Equivalent by hand:

```powershell
$env:ANTHROPIC_BASE_URL="http://127.0.0.1:4142"
$env:ANTHROPIC_AUTH_TOKEN="dummy"           # accepted, never validated (localhost)
$env:ANTHROPIC_MODEL="claude-sonnet-4-5"
$env:CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1"
claude -p "..."
```

## Endpoints

| Method | Path | Notes |
|---|---|---|
| POST | `/v1/messages` | Anthropic Messages API, stream and non-stream; query string ignored (`?beta=true`) |
| POST | `/v1/messages/count_tokens` | estimate only: `ceil(chars/4)` over system + tools + messages |
| GET | `/v1/models` | the Node proxy's list, proxied |
| GET | `/healthz` | `{"status":"ok","upstream":<bool>}` — always 200, reachability in the body |
| * | anything else | 404, logged and tapped, so an unanticipated Claude Code call is visible |

## Model mapping

Normalisation first: lowercase, strip `[1m]` and a trailing `-YYYYMMDD`.

| Claude Code model | Upstream | Why |
|---|---|---|
| `*opus*` | `claude-opus` (`M365_GATEWAY_OPUS_MODEL`) | explicit choice; **metered** |
| `*sonnet*` | `claude-sonnet` | Sonnet 4.6 on the included scenario, unmetered — the default |
| `*haiku*` | `gpt-5.5` | background/tool-less calls (titles, summaries) |
| an id the Node proxy already advertises | passthrough | pin any tone by name |
| anything else | `DEFAULT_MODEL` | |

`FORCE_MODEL` overrides every row.

## Two costs to know

- **`claude-opus` is metered and premium-only** — on a paid account it has 40
  turns/day, one unit per turn, every tool call being a turn. On a personal
  account it is a dead route (see *Account type*). Stay on `claude-sonnet` unless
  you mean it.
- **Turns are buffered.** The upstream call is non-streaming on purpose: it is the
  only mode that returns real 400/429/502 statuses, which is what makes a
  `Disengaged` a single fatal 400 instead of a retry loop that burns quota. The
  price is that the client waits for the whole turn (typically 3–30 s, worst case
  ~160 s) before the first token. The Anthropic SDK's default timeout is 10
  minutes, so this holds.

## Account type

Only the *included* scenario is assumed. On a **personal M365 account**, which is
what the phase-1 setup was measured on:

| Mapping | On a personal account |
|---|---|
| `*sonnet*` → `claude-sonnet` (Sonnet 4.6) | works; included scenario, no priority-access budget |
| `*haiku*` → `gpt-5.5` | works (chat tone) |
| `*opus*` → `claude-opus` | **dead route**: Opus needs the paid scenario, so the proxy answers `502 model_route_unavailable` and the gateway turns that into a non-retryable 404 that names the reason |

Nothing is silently downgraded on purpose: the spec's anti-goals forbid changing
what the model or toolset is without telling the operator. If you *want* opus
requests to land on sonnet on this account, say so explicitly with
`M365_GATEWAY_OPUS_MODEL=claude-sonnet` — one env var, visible in the tap as
`model_mapped`.

Claude tones serve **without** the tool agent (that is the proxy's per-tone
routing, not this gateway's), so the live unknown for this account is whether
M365 obeys the injected tool framing next to Claude Code's own system prompt —
spec risk R2. The tap answers it per turn.

## Errors

| Upstream | To the client | Retried by Claude Code |
|---|---|---|
| 429 `m365_throttled` | 429 `rate_limit_error` | yes |
| 429 `m365_throttle_hold` / `m365_turn_budget` / `priority_access_exhausted` | 429 + `retry-after` | yes |
| 502 `disengaged` | **400** `invalid_request_error` code `m365_disengaged` | no — deliberate, to preserve quota |
| 502 `upstream_empty_response`, empty completion | 503 `overloaded_error` | yes |
| 502 `model_route_unavailable` | 404 `not_found_error` | no |
| other 5xx | 502 `api_error` | yes |
| 400 | 400, message passed through | no |

At ≥95% of the per-conversation message cap (~600), the gateway answers 400
`m365_conversation_exhausted` and tells the user to `/compact`. The check runs
after the turn — no conversation state is kept, so the percentage comes from the
turn just served.

## Measurement tap

One JSON line per request in `tap/YYYY-MM-DD.jsonl` (`TAP_DIR`, or `TAP_OFF=1`):
timestamps, requested vs mapped model, sizes, estimated input tokens, status,
error type/code, `retry-after`, stop reason, tool calls (with a `parse_failed`
flag), latency, the schema projection actually applied, and the M365 counters
(`x_m365_dea_score`, `x_m365_conversation_pct`, …). The same counters are
returned to the client as `x-m365-*` headers on every successful turn.

Derived per run: turns per task, tool calls per task, `Disengaged` rate, median
and p95 turn latency, tools whose arguments failed to parse.

## Non-goals (phase 1)

No M365 auth/WebSocket in Python (the Node proxy stays the only M365 client), no
tool or system-prompt compaction, no prompt caching, no extended thinking, no
image tool results, no multi-tool parallel calls, no gateway retries, no auth/TLS,
no remote binding, no client other than Claude Code.

One deliberate adaptation: tool parameters whose schema is `array`/`object` are
sent upstream as `<name>_json` strings and parsed back (`json.loads`, then YAML).
Without it `TodoWrite` arrives at Claude Code as a string and fails its schema —
see spec §7.9. The projection is recorded per request in the tap.

## Development

```sh
uv sync
uv run pytest              # unit + route tests against a respx stub, no network, no M365
uv run ruff check src tests
```

`scripts/stub-upstream.py` serves the Node proxy's shapes on :4141 so the real
`claude` binary can be exercised without an M365 account or any quota:

```sh
uv run python scripts/stub-upstream.py            # :4141
uv run uvicorn gateway.main:app --port 4142       # :4142
scripts/claude-m365.ps1 -p "create hello.py that prints hi, run it" --allowedTools "Bash,Write"
```

The live M365 half (spec §10) stays an operator step: it spends quota and needs a
rested account.

## Acceptance (spec §2, live account)

1. text-only prompt, streamed
2. create a file and run it (`Bash`) — one full multi-turn tool loop
3. edit an existing file (`Edit`) — `old_string`/`new_string` round-trip
4. context across turns
5. `TodoWrite` (array-of-objects parameter) uncorrupted
6. `Task` subagent (a nested request through the same gateway)
7. plausible `/context` numbers

Record per run: turns, tool calls, `Disengaged` count, wall time per turn,
`x_m365_dea_score`, and any 4xx/5xx the client had to retry.
