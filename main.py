"""
Autonomous Bug Fixer - Hybrid Escalation Edition

Strategy per bug:
  Attempt 1: Direct LLM with claude-sonnet-4-20250514
  Attempt 2: Direct LLM with claude-opus-4-20250514
  Attempt 3: Direct LLM with claude-3-5-sonnet-20240620
  Attempt 4+: Escalate to CrewAI (3 agents: PLAN → EXECUTE → OBSERVE)
              Uses claude-sonnet-4-20250514 (back to first model)

Stops immediately if cost exceeds budget ($5 default).

Run: python main.py
"""

import os
import sys
import time
from pathlib import Path
from datetime import datetime
import json
import re

from config_loader import load_config, setup_llm, setup_anthropic_llm, get_claude_model_for_iteration
from logger_setup import setup_logging, log_info, log_error, log_warning
from git_utils import setup_repository, create_snapshot, revert_to_snapshot
from test_utils import run_tests, parse_test_results, format_failure_for_agent
from cost_tracker import init_cost_tracker, get_cost_summary, switch_provider, is_over_budget

# How many direct LLM attempts before escalating to CrewAI
# 3 attempts = 1 per model (sonnet → opus → 3.5-sonnet), then CrewAI on 4th
DIRECT_LLM_ATTEMPTS = 3

# MAX_ATTEMPTS_PER_BUG is loaded from .env via config (default: 5)


# ============== DIRECT LLM FIX (SIMPLE/FAST) ==============

def try_direct_llm_fix(llm, repo_path, failure, config=None):
    """
    Try to fix a single bug with a direct LLM call.

    Returns:
        bool: True if bug was fixed, False otherwise
    """
    test_name = failure['test_name']
    test_file = failure['test_file']
    error_msg = failure['error_message']

    # Find source file
    source_file = repo_path / test_file.replace('test_', '').replace('.py', '.py')
    if not source_file.exists():
        possible_files = list(repo_path.glob("*.py"))
        source_file = None
        for f in possible_files:
            if 'test_' not in f.name and f.name.endswith('.py'):
                source_file = f
                break

    if not source_file or not source_file.exists():
        log_error(f"Could not find source file for {test_file}")
        return False

    log_info(f"Reading source file: {source_file.name}")
    with open(source_file, 'r', encoding='utf-8') as f:
        source_code = f.read()

    test_file_path = repo_path / test_file
    with open(test_file_path, 'r', encoding='utf-8') as f:
        test_code = f.read()

    prompt = f"""You are an expert Python debugger. Fix this bug:

TEST FAILURE:
{test_name}

ERROR MESSAGE:
{error_msg}

TRACEBACK:
{failure.get('traceback', 'No traceback available')}

SOURCE CODE ({source_file.name}):
```python
{source_code}
```

TEST CODE ({test_file}):
```python
{test_code}
```

INSTRUCTIONS:
1. Identify the root cause of the bug
2. Provide ONLY the COMPLETE fixed source code
3. Do NOT include explanations, just the code
4. Make MINIMAL changes - only fix the bug
5. Preserve all existing functionality

OUTPUT FORMAT:
```python
# Your fixed code here
```
"""

    try:
        log_info("Asking LLM for fix...")
        response = llm.invoke(prompt)

        if hasattr(llm, 'active_provider') and hasattr(llm, '_using_fallback') and llm._using_fallback:
            switch_provider(llm.active_provider)

        response_text = response.content if hasattr(response, 'content') else str(response)

        code_match = re.search(r'```python\n(.*?)\n```', response_text, re.DOTALL)
        if not code_match:
            code_match = re.search(r'```\n(.*?)\n```', response_text, re.DOTALL)

        if code_match:
            fixed_code = code_match.group(1)

            log_info(f"Applying fix to {source_file.name}")
            with open(source_file, 'w', encoding='utf-8') as f:
                f.write(fixed_code)

            create_snapshot(repo_path, f"Direct LLM fix attempt for {test_name}")
            return True
        else:
            log_error("Could not extract code from LLM response")
            return False

    except RuntimeError as e:
        if "BOTH PROVIDERS FAILED" in str(e):
            log_error(str(e))
            raise  # Re-raise to stop the program
        log_error(f"Error getting fix from LLM: {e}")
        return False
    except Exception as e:
        log_error(f"Error getting fix from LLM: {e}")
        return False


# ============== CREWAI FIX (HEAVY/POWERFUL) ==============

def try_crew_fix(repo_path, failure, iteration, config):
    """
    Try to fix a single bug using CrewAI multi-agent system.
    Includes rate limit retry with backoff.

    Returns:
        bool: True if bug was fixed, False otherwise
    """
    # Budget check before starting expensive crew
    if is_over_budget():
        log_error("Budget exceeded - skipping CrewAI (too expensive)")
        return False

    from crew_setup import (
        create_crew_for_iteration, create_tasks_for_bug,
        Crew, Process
    )

    test_name = failure['test_name']
    max_retries = 2
    retry_wait = 65  # seconds to wait on rate limit

    for attempt in range(max_retries):
        llm, code_analyst, bug_fixer, qa_verifier = create_crew_for_iteration(iteration, config)
        if not llm:
            log_error("Failed to create crew")
            return False

        tasks = create_tasks_for_bug(
            failure, str(repo_path),
            code_analyst, bug_fixer, qa_verifier
        )

        crew = Crew(
            agents=[code_analyst, bug_fixer, qa_verifier],
            tasks=tasks,
            process=Process.sequential,
            verbose=True,
            memory=False
        )

        try:
            log_info(f"[CREW] Starting PLAN -> EXECUTE -> OBSERVE for: {test_name}")
            result = crew.kickoff()
            log_info(f"[CREW] Result: {result}")
            return True

        except Exception as e:
            error_msg = str(e).lower()
            if 'rate_limit' in error_msg or 'rate limit' in error_msg:
                # Check if fix was already applied before the crash
                log_warning(f"[CREW] Rate limited on attempt {attempt + 1}")

                test_output = run_tests(repo_path)
                new_results = parse_test_results(test_output, repo_path)
                still_failing = any(
                    f['test_name'] == test_name for f in new_results['failures']
                )
                if not still_failing:
                    log_info(f"[CREW] Fix was applied before rate limit - bug is fixed!")
                    return True

                if attempt < max_retries - 1:
                    log_info(f"[CREW] Waiting {retry_wait}s before retry...")
                    time.sleep(retry_wait)
                    continue
            else:
                log_error(f"[CREW] Error: {e}")

                # Still check if fix was applied before crash
                test_output = run_tests(repo_path)
                new_results = parse_test_results(test_output, repo_path)
                still_failing = any(
                    f['test_name'] == test_name for f in new_results['failures']
                )
                if not still_failing:
                    log_info(f"[CREW] Fix was applied before error - bug is fixed!")
                    return True
                return False

    return False


# ============== HYBRID ESCALATION LOOP ==============

def fix_bugs_hybrid(llm, repo_path, failures, max_iterations=10, config=None):
    """
    Hybrid bug fixing: Direct LLM first, escalate to CrewAI if needed.

    Per bug:
      Attempt 1-2: Direct LLM (fast, cheap)
      Attempt 3+:  CrewAI multi-agent (PLAN → EXECUTE → OBSERVE)

    Args:
        llm: Language model instance
        repo_path: Path to repository
        failures: List of test failures
        max_iterations: Maximum total iterations
        config: Configuration dictionary

    Returns:
        int: Number of bugs fixed
    """
    bugs_fixed = 0
    current_model = None
    # Track how many times we've attempted each bug
    attempt_counts = {}
    has_crewai = None  # lazy check
    max_attempts_per_bug = config.get('max_attempts_per_bug', 5) if config else 5

    for iteration in range(max_iterations):
        if not failures:
            log_info("All bugs fixed!")
            break

        # Budget check - stop if cost exceeds limit
        if is_over_budget():
            cost_summary = get_cost_summary()
            log_error(f"BUDGET EXCEEDED! Total cost: ${cost_summary['total_cost']:.4f} "
                      f"(limit: ${cost_summary['budget_limit']:.2f})")
            log_error("Stopping bug fixer to prevent further charges.")
            break

        failure = failures[0]
        test_name = failure['test_name']

        # Track attempts per bug
        if test_name not in attempt_counts:
            attempt_counts[test_name] = 0
        attempt_counts[test_name] += 1
        attempt = attempt_counts[test_name]

        # Per-bug limit: give up after max_attempts_per_bug
        if attempt > max_attempts_per_bug:
            log_warning(f"[GIVE UP] {test_name} failed after {max_attempts_per_bug} attempts. Moving to next bug.")
            failures.pop(0)
            continue

        # Rotate Claude model based on iteration
        if config and config.get('llm_provider') == 'anthropic':
            new_model = get_claude_model_for_iteration(iteration)
            if new_model != current_model:
                current_model = new_model
                log_info(f"[MODEL ROTATION] Switching to: {current_model}")
                rotated_llm = setup_anthropic_llm(config, model_name=current_model)
                if rotated_llm:
                    llm = rotated_llm
                else:
                    log_warning(f"Failed to switch to {current_model}, keeping current model")

        log_info(f"\n{'='*80}")

        # Decide: Direct LLM or CrewAI?
        if attempt <= DIRECT_LLM_ATTEMPTS:
            # === PHASE A: Direct LLM (fast, cheap) ===
            log_info(f"Iteration {iteration + 1} | Bug: {test_name} | Attempt {attempt}/{DIRECT_LLM_ATTEMPTS} [Direct LLM]")
            log_info(f"{'='*80}")

            try:
                fix_applied = try_direct_llm_fix(llm, repo_path, failure, config)
            except RuntimeError as e:
                if "BOTH PROVIDERS FAILED" in str(e):
                    log_error("Both Anthropic and OpenAI have no balance. Stopping program.")
                    break
                raise

        else:
            # === PHASE B: Escalate to CrewAI (powerful, multi-agent) ===
            # Lazy check if CrewAI is installed
            if has_crewai is None:
                try:
                    import crewai
                    has_crewai = True
                except ImportError:
                    has_crewai = False
                    log_warning("[ESCALATION] CrewAI not installed - staying with direct LLM")
                    log_warning("[ESCALATION] Install: pip install crewai crewai-tools")

            if has_crewai:
                log_info(f"Iteration {iteration + 1} | Bug: {test_name} | Attempt {attempt} [ESCALATED to CrewAI]")
                log_info(f"[ESCALATION] Direct LLM failed {DIRECT_LLM_ATTEMPTS}x, using multi-agent system")
                log_info(f"{'='*80}")

                try:
                    fix_applied = try_crew_fix(repo_path, failure, iteration, config)
                except RuntimeError as e:
                    if "BOTH PROVIDERS FAILED" in str(e):
                        log_error("Both Anthropic and OpenAI have no balance. Stopping program.")
                        break
                    raise
            else:
                log_info(f"Iteration {iteration + 1} | Bug: {test_name} | Attempt {attempt} [Direct LLM - no CrewAI]")
                log_info(f"{'='*80}")

                try:
                    fix_applied = try_direct_llm_fix(llm, repo_path, failure, config)
                except RuntimeError as e:
                    if "BOTH PROVIDERS FAILED" in str(e):
                        log_error("Both Anthropic and OpenAI have no balance. Stopping program.")
                        break
                    raise

        # Verify: re-run tests
        if fix_applied:
            log_info("Re-running tests to verify fix...")
            test_output = run_tests(repo_path)
            new_results = parse_test_results(test_output, repo_path)

            test_still_failing = any(
                f['test_name'] == test_name for f in new_results['failures']
            )

            if not test_still_failing:
                log_info(f"[OK] Successfully fixed {test_name}! (attempt {attempt})")
                bugs_fixed += 1
            else:
                log_warning(f"[FAIL] Fix didn't work for {test_name} (attempt {attempt})")

            # Update failures with current test state
            failures = new_results['failures']
        else:
            log_warning(f"[SKIP] Could not generate fix for {test_name} (attempt {attempt})")
            # Move this bug to the back of the list so we try other bugs first
            failures.append(failures.pop(0))

    return bugs_fixed


# ============== MAIN ==============

def main():
    """Main entry point for the bug fixer"""

    start_time = datetime.now()
    log_info("=" * 80)
    log_info("AUTONOMOUS BUG FIXER - Hybrid Escalation Edition")
    log_info("=" * 80)
    log_info(f"Strategy: Direct LLM x{DIRECT_LLM_ATTEMPTS} -> then CrewAI escalation")

    # Load configuration
    config = load_config()
    if not config:
        log_error("Failed to load configuration")
        sys.exit(1)

    log_info(f"Repository: {config['repo_path']}")
    log_info(f"LLM Provider: {config['llm_provider']}")
    log_info(f"Cost Limit: ${config['cost_limit']}")
    log_info(f"Max Iterations: {config['max_iterations']}")
    log_info(f"Max Attempts Per Bug: {config.get('max_attempts_per_bug', 5)}")

    # Setup LLM
    llm = setup_llm(config)
    if not llm:
        log_error("Failed to setup LLM")
        sys.exit(1)

    # Initialize cost tracking
    init_cost_tracker(config['cost_limit'], config['llm_provider'])

    # Setup repository
    repo_path = setup_repository(config['repo_path'])
    if not repo_path:
        log_error("Failed to setup repository")
        sys.exit(1)

    log_info(f"Working directory: {repo_path}")

    # Create initial snapshot
    initial_snapshot = create_snapshot(repo_path, "Initial state before bug fixing")
    log_info(f"Initial snapshot: {initial_snapshot}")

    # ==========================================
    # Phase 1: Run initial tests
    # ==========================================
    log_info("\n" + "=" * 80)
    log_info("PHASE 1: Initial Test Analysis")
    log_info("=" * 80)

    initial_test_output = run_tests(repo_path)
    initial_results = parse_test_results(initial_test_output, repo_path)

    log_info(f"Total tests: {initial_results['total']}")
    log_info(f"Passing: {initial_results['passed']}")
    log_info(f"Failing: {initial_results['failed']}")

    if initial_results['failed'] == 0:
        log_info("[PASS] All tests passing! No bugs to fix.")
        save_session_report(config, initial_results, start_time)
        return

    log_info(f"\nFound {initial_results['failed']} failing tests:")
    for i, failure in enumerate(initial_results['failures'], 1):
        log_info(f"  {i}. {failure['test_name']}")

    # ==========================================
    # Phase 2: Fix bugs (Hybrid Escalation)
    # ==========================================
    log_info("\n" + "=" * 80)
    log_info("PHASE 2: Bug Fixing (Hybrid Escalation)")
    log_info("=" * 80)
    log_info(f"  Step 1: Try Direct LLM (1 attempt per model x{DIRECT_LLM_ATTEMPTS} models)")
    log_info(f"    Attempt 1: claude-sonnet-4-20250514")
    log_info(f"    Attempt 2: claude-opus-4-20250514")
    log_info(f"    Attempt 3: claude-3-5-sonnet-20240620")
    log_info(f"  Step 2: If still not fixed, escalate to CrewAI on attempt 4-5")
    log_info(f"  Step 3: Give up on bug after {config.get('max_attempts_per_bug', 5)} attempts, move to next bug")
    log_info(f"  Budget limit: ${config['cost_limit']:.2f} (will stop if exceeded)")

    try:
        bugs_fixed = fix_bugs_hybrid(
            llm,
            repo_path,
            initial_results['failures'].copy(),
            max_iterations=config['max_iterations'],
            config=config
        )

        log_info(f"\nFixed {bugs_fixed} bugs")

    except Exception as e:
        log_error(f"Error during bug fixing: {str(e)}")
        log_error("Reverting to initial state...")
        if initial_snapshot:
            revert_to_snapshot(repo_path, initial_snapshot)

    # ==========================================
    # Phase 3: Final verification
    # ==========================================
    log_info("\n" + "=" * 80)
    log_info("PHASE 3: Final Verification")
    log_info("=" * 80)

    final_test_output = run_tests(repo_path)
    final_results = parse_test_results(final_test_output, repo_path)

    log_info(f"Final results:")
    log_info(f"  Total: {final_results['total']}")
    log_info(f"  Passing: {final_results['passed']}")
    log_info(f"  Failing: {final_results['failed']}")

    # Summary
    end_time = datetime.now()
    duration = (end_time - start_time).total_seconds()

    log_info("\n" + "=" * 80)
    log_info("SESSION SUMMARY")
    log_info("=" * 80)
    log_info(f"Duration: {duration:.1f} seconds")
    log_info(f"Initial failures: {initial_results['failed']}")
    log_info(f"Final failures: {final_results['failed']}")
    log_info(f"Bugs fixed: {initial_results['failed'] - final_results['failed']}")

    cost_summary = get_cost_summary()
    log_info(f"Total cost: ${cost_summary['total_cost']:.4f}")
    log_info(f"Total tokens: {cost_summary['total_tokens']:,}")

    # Save session report
    save_session_report(config, {
        'initial': initial_results,
        'final': final_results,
        'cost': cost_summary,
        'duration': duration
    }, start_time)

    sys.exit(0 if final_results['failed'] == 0 else 1)


def save_session_report(config, results, start_time):
    """Save session data to JSON file"""
    output_dir = Path(config.get('output_dir', './output'))
    output_dir.mkdir(parents=True, exist_ok=True)

    session_id = f"session_{start_time.strftime('%Y%m%d_%H%M%S')}"
    report_file = output_dir / f"{session_id}.json"

    with open(report_file, 'w') as f:
        json.dump({
            'session_id': session_id,
            'timestamp': start_time.isoformat(),
            'results': results
        }, f, indent=2)

    log_info(f"Session report saved: {report_file}")


if __name__ == "__main__":
    setup_logging()

    try:
        main()
    except KeyboardInterrupt:
        log_info("\n\nInterrupted by user")
        sys.exit(1)
    except Exception as e:
        log_error(f"Fatal error: {str(e)}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
