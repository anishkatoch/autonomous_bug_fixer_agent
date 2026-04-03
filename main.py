"""
Autonomous Bug Fixer - CrewAI Multi-Agent Edition
Uses 3 agents (Analyst, Fixer, Verifier) in a PLAN -> EXECUTE -> OBSERVE loop.
Falls back to direct LLM if CrewAI is not available.

Run: python main.py
"""

import os
import sys
from pathlib import Path
from datetime import datetime
import json

from config_loader import load_config, setup_llm, setup_anthropic_llm, get_claude_model_for_iteration
from logger_setup import setup_logging, log_info, log_error, log_warning
from git_utils import setup_repository, create_snapshot, revert_to_snapshot
from test_utils import run_tests, parse_test_results, format_failure_for_agent
from cost_tracker import init_cost_tracker, get_cost_summary, switch_provider


# ============== FALLBACK: DIRECT LLM LOOP ==============

def fix_bugs_with_llm(llm, repo_path, failures, max_iterations=10, config=None):
    """
    Fallback: Use LLM directly (without CrewAI) to fix bugs.
    Used when CrewAI is not installed.
    """
    bugs_fixed = 0
    current_model = None

    for iteration in range(max_iterations):
        if not failures:
            log_info("All bugs fixed!")
            break

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

        failure = failures[0]
        test_name = failure['test_name']
        test_file = failure['test_file']
        error_msg = failure['error_message']

        log_info(f"\n{'='*80}")
        log_info(f"Iteration {iteration + 1}: Fixing {test_name} [Direct LLM]")
        log_info(f"{'='*80}")

        # Read the source file
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
            failures.pop(0)
            continue

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

            import re
            code_match = re.search(r'```python\n(.*?)\n```', response_text, re.DOTALL)
            if not code_match:
                code_match = re.search(r'```\n(.*?)\n```', response_text, re.DOTALL)

            if code_match:
                fixed_code = code_match.group(1)

                log_info(f"Applying fix to {source_file.name}")
                with open(source_file, 'w', encoding='utf-8') as f:
                    f.write(fixed_code)

                create_snapshot(repo_path, f"Fix attempt for {test_name}")

                log_info("Re-running tests...")
                test_output = run_tests(repo_path)
                new_results = parse_test_results(test_output, repo_path)

                test_still_failing = any(f['test_name'] == test_name for f in new_results['failures'])

                if not test_still_failing:
                    log_info(f"[OK] Successfully fixed {test_name}!")
                    bugs_fixed += 1
                    failures.pop(0)
                else:
                    log_warning(f"[FAIL] Fix didn't work for {test_name}")
                    failures.pop(0)

                failures = new_results['failures']
            else:
                log_error("Could not extract code from LLM response")
                failures.pop(0)

        except Exception as e:
            log_error(f"Error getting fix from LLM: {e}")
            failures.pop(0)
            continue

    return bugs_fixed


# ============== MAIN ==============

def main():
    """Main entry point for the bug fixer"""

    start_time = datetime.now()
    log_info("=" * 80)
    log_info("AUTONOMOUS BUG FIXER - CrewAI Multi-Agent Edition")
    log_info("=" * 80)

    # Load configuration
    config = load_config()
    if not config:
        log_error("Failed to load configuration")
        sys.exit(1)

    log_info(f"Repository: {config['repo_path']}")
    log_info(f"LLM Provider: {config['llm_provider']}")
    log_info(f"Cost Limit: ${config['cost_limit']}")

    # Setup LLM (used as fallback if CrewAI fails)
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
    # Phase 2: Fix bugs (CrewAI or fallback)
    # ==========================================
    log_info("\n" + "=" * 80)
    log_info("PHASE 2: Bug Fixing")
    log_info("=" * 80)

    try:
        # Try CrewAI first
        try:
            from crew_setup import fix_bugs_with_crew
            log_info("[CREW] CrewAI available - using multi-agent system")
            log_info("[CREW] Agents: Code Analyst (PLAN) -> Bug Fixer (EXECUTE) -> QA Verifier (OBSERVE)")

            bugs_fixed = fix_bugs_with_crew(
                repo_path,
                initial_results['failures'].copy(),
                max_iterations=config['max_iterations'],
                config=config
            )

        except ImportError as e:
            log_warning(f"[FALLBACK] CrewAI not available ({e}), using direct LLM approach")
            log_warning("[FALLBACK] Install CrewAI: pip install crewai crewai-tools")

            bugs_fixed = fix_bugs_with_llm(
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
