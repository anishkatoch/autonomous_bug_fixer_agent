# Design Document: Autonomous Bug Fixer

## Architecture Overview

The system is a fully autonomous bug fixer that takes a repository path, runs its tests, and fixes failing tests without human intervention. It is built as a set of plain Python modules — no frameworks other than LangChain (for LLM wrappers) and optionally CrewAI (for multi-agent escalation).

```
main.py                  Orchestrator — runs the 6-step loop, tracks bugs, produces reports
config_loader.py         Loads .env, sets up LLM (Anthropic primary, OpenAI fallback)
test_utils.py            Runs pytest, parses JSON or text output into structured results
git_utils.py             Git init, snapshot (commit), revert (reset --hard), diff
cost_tracker.py          Tracks token usage and cost per request, enforces budget limit
logger_setup.py          Dual logging: console (INFO) + file (DEBUG) with timestamps
crew_setup.py            CrewAI agents and tools — used only when direct LLM fails
```

Data flows in one direction through the loop:

```
 pytest output                    LLM prompt                     patched file
      |                               |                               |
 test_utils.py -----> main.py -----> LLM -----> main.py -----> source file
 (run + parse)     (build prompt)          (extract code)     (write + verify)
                        ^                                          |
                        |                                          v
                   git_utils.py <----- main.py <----- test_utils.py
                   (revert if bad)   (check results)   (re-run tests)
```

There are no shared global objects between modules except the logger and cost tracker (both initialised once at startup). Each module exposes plain functions; the only class in the system is `BugTracker` in main.py (tracks attempt history) and `FallbackLLM` in config_loader.py (wraps two LLMs with automatic failover).

---

## Agent Loop and Control Flow

The core loop implements six concrete steps. Every step is logged to both console and the log file.

### Step 1 — Run tests, identify failures

```
pytest -v --tb=short --json-report .
```

Output is parsed into a list of failure dicts, each containing `test_name`, `test_file`, `error_message`, and `traceback`. If the JSON report plugin is unavailable, a regex-based text parser extracts the same fields from stdout.

### Step 2 — Analyse failure and localise the bug

For each failing test, the system:

1. Finds the source file (strips `test_` prefix, or scans for non-test `.py` files)
2. Reads both source and test code
3. Builds a prompt containing: test name, error message, full traceback, source code, test code, and any previous failed attempts for this bug
4. Sends to the LLM and extracts the code block from the response

The prompt explicitly tells the LLM to decide whether the bug is in the source or the test (e.g. a missing mock). It outputs a tagged code block (`python:source` or `python:test`) so the system knows which file to patch.

### Step 3 — Generate and apply the patch

The extracted code replaces the entire target file (full-file replacement, not line-level patching). A diff summary is computed (`+N lines, -M lines`) and logged. A git commit is created immediately after writing.

### Step 4 — Re-run tests and verify

Tests are re-run. Two checks:

- **Target test**: does the specific failing test now pass?
- **Regressions**: did any previously-passing test start failing?

Both are logged with details.

### Step 5 — Revert if fix fails or causes regressions

If the target test still fails, or if regressions are detected, the system writes back the original code, creates a revert commit, and re-runs tests to confirm clean state. The failure reason is recorded in the bug tracker so the next attempt can try a different approach.

### Step 6 — Repeat or report

The loop continues with the next bug or the next attempt on the same bug. It stops when:

- All tests pass
- A per-bug attempt limit is reached (default 5) — gives up on that bug, moves to the next
- Total iteration limit is reached (default 10)
- Budget is exceeded

Any unresolved bug gets a detailed explanation in the logs and the JSON report: original error, every attempt's diff and failure reason, and why the system gave up.

### Escalation within the loop

```
Attempt 1:  Direct LLM  (claude-sonnet-4-20250514)
Attempt 2:  Direct LLM  (claude-opus-4-20250514)
Attempt 3:  Direct LLM  (claude-3-5-sonnet-20240620)
Attempt 4+: CrewAI       (3-agent system: Analyst -> Fixer -> Verifier)
```

Each direct LLM attempt rotates the model so the same bug sees different reasoning styles. If all three fail, CrewAI is used — three agents with separate tools run a PLAN-EXECUTE-OBSERVE pipeline with their own internal tool calls (read file, write file, run tests, create snapshot, revert, get diff).

---

## Tool / Function Design

### Why plain functions, not classes

Every module (test_utils, git_utils, cost_tracker, logger_setup) exposes standalone functions. This keeps call sites obvious — `run_tests(repo_path)` is clearer than `runner.run()` and easier to test in isolation.

The two exceptions are `BugTracker` (accumulates state across iterations) and `FallbackLLM` (must subclass LangChain's `BaseChatModel`).

### Key functions and what they do

| Function | Module | Purpose |
|----------|--------|---------|
| `run_tests(repo_path)` | test_utils | Runs pytest, returns raw output |
| `parse_test_results(output, repo_path)` | test_utils | Parses output into `{total, passed, failed, failures[]}` |
| `analyse_failure(llm, repo_path, failure, prev_attempts)` | main | Reads files, prompts LLM, extracts fix code |
| `apply_patch(repo_path, fix_data)` | main | Writes fixed code, creates git snapshot |
| `verify_fix(repo_path, test_name, prev_passing)` | main | Re-runs tests, checks target + regressions |
| `revert_fix(repo_path, fix_data, reason)` | main | Restores original code, logs reason |
| `create_snapshot(repo_path, message)` | git_utils | `git add -A && git commit` |
| `revert_to_snapshot(repo_path, hash)` | git_utils | `git reset --hard <hash>` |
| `track_tokens(input, output, context)` | cost_tracker | Calculates cost, checks budget |

### CrewAI tools (used only in escalation)

Six `BaseTool` subclasses in crew_setup.py wrap the same plain functions above: `RunTestsTool`, `ReadFileTool`, `WriteFileTool`, `CreateSnapshotTool`, `RevertChangesTool`, `GetDiffTool`. Each has a Pydantic input schema so CrewAI agents can call them autonomously.

---

## Retry and Recovery Strategy

### Three layers of protection

**Layer 1 — Per-attempt revert.** Every fix attempt is bracketed by a snapshot. If the fix fails or causes regressions, the original code is restored before the next attempt. The system never accumulates broken state.

**Layer 2 — Per-bug retry with context.** Each bug gets up to 5 attempts. Failed attempt details (what was tried, what changed, why it failed) are fed back into the prompt so the LLM tries a different approach. Model rotation ensures the same bug sees different reasoning.

**Layer 3 — Escalation.** If 3 direct LLM attempts fail, the system escalates to CrewAI's multi-agent pipeline where three specialised agents collaborate. This catches bugs that need deeper analysis than a single prompt can provide.

### What gets reverted and when

| Situation | Action |
|-----------|--------|
| Fix applied, target test still fails | Revert not automatic — code stays, next attempt overwrites |
| Fix applied, regression detected | Immediate revert to original, re-run tests to confirm clean state |
| Fix applied, CrewAI error mid-way | Check if fix worked despite error; revert if not |
| Budget exceeded | Stop immediately, keep whatever fixes were already verified |
| Both API providers fail | Stop immediately, log which bugs remain |

### Rate limit handling

CrewAI attempts retry up to 2 times with a 65-second wait on rate limit errors. Before retrying, the system checks if a partial fix was already applied (the LLM may have written the file before the rate limit hit on a later tool call).

---

## LLM Selection Rationale

### Primary: Anthropic Claude (3 models rotated)

| Model | Role | Why |
|-------|------|-----|
| claude-sonnet-4 | Attempt 1 (and CrewAI) | Best cost/quality ratio for code. Fast. |
| claude-opus-4 | Attempt 2 | Strongest reasoning. Handles complex bugs that sonnet misses. |
| claude-3.5-sonnet | Attempt 3 | Different training cut — sometimes catches what newer models don't. |

Rotation is the single most impactful retry strategy. The same bug with the same prompt often gets a correct fix from a different model after the first one fails. This costs nothing extra in engineering complexity.

### Fallback: OpenAI GPT-4o

If Anthropic has no balance, auth errors, or rate limits, `FallbackLLM` transparently switches all calls to GPT-4o. The switch is logged and permanent for the session (no flip-flopping). Cost tracking updates to OpenAI pricing automatically.

### Why not a single model?

A single model gets stuck in the same reasoning rut on retry. Different models have different failure modes — opus is better at complex multi-step reasoning, sonnet is better at surgical one-line fixes, 3.5-sonnet sometimes spots patterns the newer models overfit past. Rotating costs nothing (same API, same key) and measurably improves fix rates.

---

## What I Would Improve With More Time

### High impact, moderate effort

**Targeted test runs.** Currently the system re-runs the entire test suite after every fix attempt. With test-impact analysis (tracing which source functions each test calls), it could run only the affected tests — cutting verification time from 20s to under 2s per attempt.

**Diff-level patching instead of full-file replacement.** The current approach replaces the entire source file. This works but means the LLM must reproduce hundreds of unchanged lines correctly. A diff-based approach (send only the relevant function, get back only the changed lines) would reduce token usage by 60-70% and eliminate the risk of the LLM accidentally dropping unrelated code.

**Structured output parsing.** The current regex extraction (````python ... ```) is fragile. Using the LLM's structured output / tool-use mode to return `{"file": "...", "code": "..."}` would eliminate parsing failures entirely.

### High impact, high effort

**Parallel bug fixing.** Independent bugs (those that touch different source files) could be fixed concurrently in separate git branches, then merged. This would cut total wall-clock time roughly in proportion to the number of independent bugs.

**Learning from history.** Persisting a database of (error pattern, successful fix pattern) pairs across sessions. Before prompting the LLM, check if a similar error was fixed before and include that fix as a hint. This would reduce LLM calls for recurring bug patterns to near zero.

**AST-aware patching.** Instead of treating source code as text, parse it into an AST, identify the faulty node, and ask the LLM to fix only that node. This would make patches more precise and eliminate the class of errors where the LLM changes whitespace, reorders imports, or drops comments.

### Low effort, nice to have

**Cost estimation before each attempt.** Use the prompt length to estimate cost and skip attempts that would exceed remaining budget, instead of discovering the overrun after the API call.

**HTML report generation.** The JSON session report contains all the data; rendering it as a styled HTML page with collapsible sections for each bug would make it much easier to review results.

**Webhook / CI integration.** Trigger the bug fixer from a failing CI pipeline, have it push a fix branch and open a PR automatically.
