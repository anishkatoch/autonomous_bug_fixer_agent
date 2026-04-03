# Autonomous Bug Fixer

An autonomous agent that runs a project's test suite, identifies failing tests, generates code fixes using LLMs, applies them, verifies the results, and reverts anything that breaks. No human intervention required.

---

## Quick Start — Single Command

```bash
pip install -r requirements.txt && python main.py
```

That's it. The system reads `.env` for configuration, runs tests, fixes bugs, and writes a full report to `logs/` and `output/`.

---

## Setup Instructions

### Prerequisites

- Python 3.9 or higher
- Git installed and on PATH
- An API key for Anthropic (preferred) or OpenAI (fallback)

### 1. Install dependencies

```bash
cd autonomous_bug_fixer
pip install -r requirements.txt
```

Core dependencies:

| Package | Purpose |
|---------|---------|
| `langchain-anthropic` | Claude LLM wrapper |
| `langchain-openai` | GPT-4o fallback wrapper |
| `python-dotenv` | Loads `.env` file |
| `pytest` | Runs the test suite |
| `pytest-json-report` | Structured test output (optional but recommended) |
| `crewai` | Multi-agent escalation (optional) |

If the target repository has its own dependencies (e.g. `numpy`, `fastapi`), install those too.

### 2. Configure environment variables

Create a `.env` file in the project root (or edit the existing one):

```bash
# ============== REQUIRED ==============

# Path to the repository you want to fix
# Can be a local path or a Git URL
REPO_PATH=C:/path/to/your/repo
# REPO_PATH=https://github.com/user/repo.git

# At least ONE API key is required
ANTHROPIC_API_KEY=sk-ant-api03-your-key-here
OPENAI_API_KEY=sk-proj-your-key-here

# ============== OPTIONAL ==============

# Maximum spend in USD (default: 5.0)
COST_LIMIT=5.0

# Maximum total loop iterations across all bugs (default: 20)
MAX_ITERATIONS=10

# Maximum attempts per single bug before giving up (default: 5)
# Attempts 1-3: Direct LLM (one per model)
# Attempts 4-5: CrewAI multi-agent
MAX_ATTEMPTS_PER_BUG=5

# Directory for JSON session reports (default: ./output)
OUTPUT_DIR=./output

# Log level: DEBUG, INFO, WARNING, ERROR (default: INFO)
LOG_LEVEL=INFO
```

### 3. Run

```bash
python main.py
```

---

## Environment Variables Reference

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `REPO_PATH` | Yes | — | Local path or Git URL to the repository with failing tests |
| `ANTHROPIC_API_KEY` | One of these | — | Anthropic Claude API key |
| `OPENAI_API_KEY` | One of these | — | OpenAI API key (used as fallback if both are set) |
| `COST_LIMIT` | No | `5.0` | Maximum USD to spend on LLM calls |
| `MAX_ITERATIONS` | No | `20` | Total loop iterations across all bugs |
| `MAX_ATTEMPTS_PER_BUG` | No | `5` | How many times to retry a single bug before giving up |
| `OUTPUT_DIR` | No | `./output` | Where to save JSON session reports |
| `LOG_LEVEL` | No | `INFO` | Logging verbosity (DEBUG shows LLM prompts/responses) |

If both API keys are provided, Anthropic is used as primary and OpenAI as automatic fallback. If Anthropic fails (no balance, auth error, rate limit), all subsequent calls switch to OpenAI transparently.

---

## What It Does

```
Step 1  Run pytest, parse output, list every failing test
Step 2  For each failure: read source + test code, send error/traceback to LLM
Step 3  Extract the fix from LLM response, compute diff, write to file
Step 4  Re-run tests — check if the target test passes and no regressions
Step 5  If fix fails or causes regressions — revert to original code
Step 6  Retry with a different model, or escalate to CrewAI multi-agent
        Repeat until all tests pass, or report why each bug couldn't be fixed
```

### Escalation strategy

```
Attempt 1:  Direct LLM   claude-sonnet-4
Attempt 2:  Direct LLM   claude-opus-4
Attempt 3:  Direct LLM   claude-3.5-sonnet
Attempt 4+: CrewAI        3-agent pipeline (Analyst -> Fixer -> Verifier)
```

Each attempt uses a different model so the same bug sees different reasoning. Failed attempts are fed back as context so the next attempt tries a different approach.

---

## Output

### Console + log file

Every step is logged to both the console and `logs/bug_fixer_<timestamp>.log`:

```
12:30:01 | INFO | STEP 1: Run test suite and identify failing tests
12:30:15 | INFO | Total tests:  10
12:30:15 | INFO | Failing:      6
12:30:15 | INFO |   1. test_search_bugs.py::test_bug_1_normalize_matrix_wrong_axis
12:30:15 | INFO |      Error: Row 0 has norm 0.604, expected 1.0 (wrong axis used)
...
12:30:16 | INFO | ITERATION 1/10 | Bug: test_bug_1_normalize_matrix_wrong_axis
12:30:16 | INFO |   Attempt 1/5 | Method: Direct LLM | Model: claude-sonnet-4
12:30:16 | INFO |   [ANALYSE] Reading source: search_engine_fastapi.py
12:30:18 | INFO |   [ANALYSE] Source fix generated: +1 lines, -1 lines
12:30:18 | INFO |   [PATCH] Applying fix to: search_engine_fastapi.py
12:30:18 | INFO |   [VERIFY] Target test PASSES, no regressions. Fix is good.
```

If a bug can't be resolved, the logs explain exactly why:

```
12:31:45 | WARNING |   [GIVE UP] test_bug_2 -- could not fix after 5 attempts
12:31:45 | WARNING |     Attempt 1: Fix applied but test still fails
12:31:45 | WARNING |     Attempt 2 (reverted): Fix caused 2 regression(s)
12:31:45 | WARNING |     Attempt 3: LLM response did not contain valid code block
...
12:32:00 | INFO | WHY NOT RESOLVED:
12:32:00 | INFO |   Exhausted all 5 attempts. Failure details:
12:32:00 | INFO |     - Attempt 1 (claude-sonnet-4): Fix applied but test still fails
12:32:00 | INFO |     - Attempt 2 (claude-opus-4) [REVERTED]: Caused 2 regressions
12:32:00 | INFO |     - Attempt 3 (claude-3.5-sonnet): No valid code block in response
```

### JSON session report

Saved to `output/session_<timestamp>.json` with full results:

```json
{
  "session_id": "session_20260404_123000",
  "results": {
    "initial": { "total": 10, "passed": 4, "failed": 6 },
    "final":   { "total": 10, "passed": 10, "failed": 0 },
    "cost":    { "total_cost": 2.34, "total_tokens": 98000 },
    "duration": 245.3,
    "bug_report": {
      "total_bugs": 6,
      "resolved_count": 6,
      "unresolved_count": 0,
      "resolved_bugs": [ ... ],
      "unresolved_bugs": [ ... ]
    }
  }
}
```

---

## Project Structure

```
autonomous_bug_fixer/
  main.py               Orchestrator — the 6-step autonomous loop
  config_loader.py      Loads .env, sets up LLM with Anthropic/OpenAI fallback
  test_utils.py         Runs pytest, parses JSON or text output
  git_utils.py          Git init, snapshot (commit), revert (reset --hard)
  cost_tracker.py       Tracks token usage and cost, enforces budget limit
  logger_setup.py       Dual logging: console (INFO) + file (DEBUG)
  crew_setup.py         CrewAI agents and tools (used for escalation)
  diagnose_pytest.py    Diagnostic script — run if tests aren't working
  requirements.txt      Python dependencies
  .env                  Configuration (API keys, repo path, limits)
  DESIGN.md             Architecture and design decisions
  README.md             This file

  testing/              Example target repository with intentional bugs
    search_engine_fastapi.py    Source code (6 bugs planted)
    test_search_bugs.py         Test suite (10 tests, 6 catch the bugs)

  logs/                 Execution logs (created at runtime)
  output/               JSON session reports (created at runtime)
```

---

## Diagnostics

If something isn't working, run the diagnostic script:

```bash
python diagnose_pytest.py
```

It checks: Python version, pytest installation, plugin availability, `.env` configuration, test file discovery, and runs a trial pytest execution with detailed output.

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `No test files found` | Set `REPO_PATH` to the directory containing `test_*.py` files |
| `ModuleNotFoundError` | Install the target repo's dependencies (`pip install numpy pandas` etc.) |
| `BOTH PROVIDERS FAILED` | Check API keys in `.env`, verify billing at console.anthropic.com or platform.openai.com |
| `Budget exceeded` | Increase `COST_LIMIT` in `.env` |
| `pytest not found` | Run `pip install pytest pytest-json-report` |
| Tests pass but shouldn't | The system needs failing tests — it fixes bugs that tests catch |
