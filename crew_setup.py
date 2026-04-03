"""
CrewAI Setup - Multi-Agent Bug Fixing System
PLAN → EXECUTE → OBSERVE → SELF-CORRECT

Agents:
- Code Analyst: Analyzes bugs, identifies root cause (PLAN)
- Bug Fixer: Applies minimal fixes (EXECUTE)
- QA Verifier: Verifies fixes, detects regressions (OBSERVE)

Functions:
- create_bug_fixing_crew(): Create the 3-agent crew
- create_tasks_for_bug(): Create dynamic tasks for a specific bug
- fix_bugs_with_crew(): Main loop that uses CrewAI to fix bugs
"""

from crewai import Agent, Task, Crew, Process
from crewai.tools import BaseTool
from typing import Type
from pydantic import BaseModel, Field
from pathlib import Path

from logger_setup import log_info, log_debug, log_warning, log_error, log_bug_fix_attempt
from test_utils import run_tests, parse_test_results, format_failure_for_agent
from git_utils import create_snapshot, revert_to_snapshot, get_diff
from cost_tracker import track_tokens
from config_loader import get_claude_model_for_iteration, setup_anthropic_llm


# ============== TOOL INPUT SCHEMAS ==============

class FilePathInput(BaseModel):
    file_path: str = Field(..., description="Path to the file")

class RepoPathInput(BaseModel):
    repo_path: str = Field(..., description="Path to the repository")

class WriteFileInput(BaseModel):
    file_path: str = Field(..., description="Path to the file to write")
    content: str = Field(..., description="Content to write to the file")

class SnapshotInput(BaseModel):
    repo_path: str = Field(..., description="Path to the repository")
    message: str = Field(..., description="Commit message")

class RevertInput(BaseModel):
    repo_path: str = Field(..., description="Path to the repository")
    commit_hash: str = Field(..., description="Commit hash to revert to")

class DiffInput(BaseModel):
    repo_path: str = Field(..., description="Path to the repository")
    file_path: str = Field(None, description="Optional specific file path")


# ============== CUSTOM TOOLS ==============

class RunTestsTool(BaseTool):
    name: str = "Run Tests Tool"
    description: str = (
        "Run pytest test suite in a repository and return formatted results. "
        "Use this to check which tests are passing or failing. "
        "Input: repo_path (string)"
    )
    args_schema: Type[BaseModel] = RepoPathInput

    def _run(self, repo_path: str) -> str:
        log_info("Agent using Run Tests tool...")
        output = run_tests(repo_path)
        results = parse_test_results(output, repo_path)

        summary = f"""
TEST RESULTS:
- Total: {results['total']}
- Passed: {results['passed']}
- Failed: {results['failed']}
- Skipped: {results['skipped']}

"""
        if results['failures']:
            summary += "FAILURES:\n"
            for failure in results['failures']:
                summary += format_failure_for_agent(failure) + "\n\n"

        return summary


class ReadFileTool(BaseTool):
    name: str = "Read File Tool"
    description: str = (
        "Read the contents of a source code file. "
        "Use this to examine code that needs to be fixed. "
        "Input: file_path (string)"
    )
    args_schema: Type[BaseModel] = FilePathInput

    def _run(self, file_path: str) -> str:
        log_debug(f"Agent reading file: {file_path}")
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            return f"FILE: {file_path}\n{'='*60}\n{content}\n{'='*60}"
        except Exception as e:
            return f"ERROR reading {file_path}: {str(e)}"


class WriteFileTool(BaseTool):
    name: str = "Write File Tool"
    description: str = (
        "Write new content to a file (applies the fix). "
        "Use this to save your bug fixes. "
        "Input: file_path (string), content (string)"
    )
    args_schema: Type[BaseModel] = WriteFileInput

    def _run(self, file_path: str, content: str) -> str:
        log_info(f"Agent writing to file: {file_path}")
        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(content)
            lines = content.count('\n')
            log_info(f"[OK] File updated: {file_path} ({lines} lines)")
            return f"SUCCESS: Updated {file_path} ({lines} lines)"
        except Exception as e:
            log_debug(f"ERROR writing file: {e}")
            return f"ERROR: Could not write to {file_path}: {str(e)}"


class CreateSnapshotTool(BaseTool):
    name: str = "Create Git Snapshot Tool"
    description: str = (
        "Create a Git commit snapshot of the current state. "
        "Use this after applying a fix to enable rollback. "
        "Input: repo_path (string), message (string)"
    )
    args_schema: Type[BaseModel] = SnapshotInput

    def _run(self, repo_path: str, message: str) -> str:
        log_debug(f"Creating Git snapshot: {message}")
        commit_hash = create_snapshot(repo_path, message)
        if commit_hash:
            return f"Snapshot created: {commit_hash[:8]}"
        return "ERROR: Could not create snapshot"


class RevertChangesTool(BaseTool):
    name: str = "Revert Changes Tool"
    description: str = (
        "Revert to a previous Git commit (rollback). "
        "Use this when a fix doesn't work or causes regressions. "
        "Input: repo_path (string), commit_hash (string)"
    )
    args_schema: Type[BaseModel] = RevertInput

    def _run(self, repo_path: str, commit_hash: str) -> str:
        log_info(f"Agent reverting to: {commit_hash[:8]}")
        success = revert_to_snapshot(repo_path, commit_hash)
        if success:
            return f"Reverted to {commit_hash[:8]}"
        return "ERROR: Could not revert"


class GetDiffTool(BaseTool):
    name: str = "Get File Diff Tool"
    description: str = (
        "Get Git diff showing what changed. "
        "Use this to verify what was modified. "
        "Input: repo_path (string), file_path (optional string)"
    )
    args_schema: Type[BaseModel] = DiffInput

    def _run(self, repo_path: str, file_path: str = None) -> str:
        log_debug(f"Getting diff for: {file_path or 'all files'}")
        diff = get_diff(repo_path, file_path)
        return diff if diff else "No changes"


# ============== AGENT CREATION ==============

def create_code_analyst_agent(llm):
    """Create Code Analyst Agent - The PLANNER"""
    return Agent(
        role='Senior Code Analyst & Debugger',
        goal='Analyze test failures to identify root causes and plan fix strategies',
        backstory="""You are a 20-year veteran software engineer with exceptional
        debugging skills. You've debugged thousands of complex systems and can
        trace errors through intricate code paths. You think systematically,
        consider edge cases, and always identify the ROOT CAUSE rather than
        just symptoms. You provide clear, actionable fix strategies.""",
        verbose=True,
        allow_delegation=False,
        tools=[RunTestsTool(), ReadFileTool(), GetDiffTool()],
        llm=llm
    )


def create_bug_fixer_agent(llm):
    """Create Bug Fixer Agent - The EXECUTOR"""
    return Agent(
        role='Expert Bug Fixer & Code Surgeon',
        goal='Apply minimal, elegant fixes to bugs based on root cause analysis',
        backstory="""You are a master at writing clean, minimal code fixes. You
        never over-engineer solutions. Your patches are surgical - changing only
        what's necessary to fix the bug. You preserve the original code style and
        architecture. You think about edge cases and ensure your fixes are robust.""",
        verbose=True,
        allow_delegation=True,
        tools=[ReadFileTool(), WriteFileTool(), CreateSnapshotTool(), GetDiffTool()],
        llm=llm
    )


def create_qa_verifier_agent(llm):
    """Create QA Verifier Agent - The OBSERVER"""
    return Agent(
        role='QA Verification Specialist',
        goal='Verify fixes work correctly and no regressions are introduced',
        backstory="""You are meticulous and skeptical. You test edge cases that
        others forget. You catch regressions before they reach production. You
        never accept a fix without thorough verification. You think about what
        could go wrong and test those scenarios.""",
        verbose=True,
        allow_delegation=False,
        tools=[RunTestsTool(), ReadFileTool(), RevertChangesTool()],
        llm=llm
    )


# ============== DYNAMIC TASK CREATION ==============

def create_tasks_for_bug(failure, repo_path, code_analyst, bug_fixer, qa_verifier):
    """
    Create the 3 sequential tasks for fixing a single bug.

    Returns:
        list[Task]: [analyze_task, fix_task, verify_task]
    """
    test_name = failure['test_name']
    test_file = failure['test_file']
    error_msg = failure['error_message']
    traceback_info = failure.get('traceback', 'No traceback available')

    # TASK 1: PLAN - Analyze the bug
    analyze_task = Task(
        description=f"""Analyze this test failure and identify the root cause.

TEST: {test_name}
FILE: {test_file}
ERROR: {error_msg}
TRACEBACK:
{traceback_info}

REPOSITORY PATH: {repo_path}

Steps:
1. Use the Read File Tool to read the test file at: {repo_path}/{test_file}
2. Identify the source file being tested (usually the test file name without 'test_' prefix)
3. Use the Read File Tool to read the source file
4. Analyze the error message and traceback carefully
5. Identify the ROOT CAUSE of the failure

Provide:
- Root cause explanation
- Exact file and line that needs fixing
- Specific fix strategy (what code to change and how)
- Confidence level (high/medium/low)""",
        expected_output="A detailed root cause analysis with specific fix strategy including exact file path, what to change, and the corrected code.",
        agent=code_analyst
    )

    # TASK 2: EXECUTE - Fix the bug
    fix_task = Task(
        description=f"""Apply the fix based on the analysis from the Code Analyst.

REPOSITORY PATH: {repo_path}
TARGET TEST: {test_name}

Steps:
1. Read the analysis from the previous task carefully
2. Use the Read File Tool to read the source file that needs fixing
3. Write the COMPLETE fixed source code using the Write File Tool
4. Use the Create Git Snapshot Tool to save the fix with message: "Fix for {test_name}"
5. Use the Get File Diff Tool to verify your changes look correct

IMPORTANT:
- Make MINIMAL changes - only fix the bug, don't refactor
- Write the COMPLETE file content, not just the changed lines
- Preserve all existing functionality and code style
- Do NOT modify test files""",
        expected_output="Confirmation that the fix was applied, with the file path, a summary of changes made, and the git snapshot hash.",
        agent=bug_fixer
    )

    # TASK 3: OBSERVE - Verify the fix
    verify_task = Task(
        description=f"""Verify that the fix works and no regressions were introduced.

REPOSITORY PATH: {repo_path}
TARGET TEST: {test_name}

Steps:
1. Use the Run Tests Tool with repo_path: {repo_path}
2. Check if {test_name} now PASSES
3. Check if any previously passing tests now FAIL (regressions)
4. If the fix FAILED or caused regressions, use the Revert Changes Tool

Report:
- Whether the target test now passes
- Whether any regressions were introduced
- Overall test results (passed/failed counts)
- If reverted, explain why""",
        expected_output="A verification report: target test status (PASS/FAIL), regression check results, overall test counts, and whether the fix was kept or reverted.",
        agent=qa_verifier
    )

    return [analyze_task, fix_task, verify_task]


# ============== CREW CREATION WITH MODEL ROTATION ==============

def create_crew_for_iteration(iteration, config):
    """
    Create a CrewAI crew with the correct model for this iteration.

    Model rotation (Claude only):
      Iterations 0-1: claude-sonnet-4-20250514
      Iterations 2-3: claude-opus-4-20250514
      Iterations 4-5: claude-3-5-sonnet-20240620
      Iterations 6+:  claude-sonnet-4-20250514
    """
    if config.get('llm_provider') == 'anthropic':
        model_name = get_claude_model_for_iteration(iteration)
        log_info(f"[MODEL ROTATION] Iteration {iteration + 1}: using {model_name}")
        llm = setup_anthropic_llm(config, model_name=model_name)
        if not llm:
            log_error(f"Failed to create LLM for model {model_name}")
            return None, None, None, None
    else:
        # For OpenAI or fallback, use the passed-in LLM setup
        from config_loader import setup_llm
        llm = setup_llm(config)
        if not llm:
            return None, None, None, None

    code_analyst = create_code_analyst_agent(llm)
    bug_fixer = create_bug_fixer_agent(llm)
    qa_verifier = create_qa_verifier_agent(llm)

    return llm, code_analyst, bug_fixer, qa_verifier


# ============== MAIN CREW BUG FIXING LOOP ==============

def fix_bugs_with_crew(repo_path, failures, max_iterations=10, config=None):
    """
    Use CrewAI multi-agent system to fix bugs.

    Flow per bug:
      1. Create crew with rotated model
      2. Create dynamic tasks for the bug
      3. Kick off crew: PLAN → EXECUTE → OBSERVE
      4. Check results and move to next bug

    Args:
        repo_path: Path to repository
        failures: List of test failures
        max_iterations: Maximum fix attempts
        config: Configuration dictionary

    Returns:
        int: Number of bugs fixed
    """
    bugs_fixed = 0
    current_model = None

    for iteration in range(max_iterations):
        if not failures:
            log_info("All bugs fixed!")
            break

        failure = failures[0]
        test_name = failure['test_name']

        log_info(f"\n{'='*80}")
        log_info(f"Iteration {iteration + 1}/{max_iterations}: Fixing {test_name}")
        log_info(f"{'='*80}")

        # Create crew with model rotation
        new_model = get_claude_model_for_iteration(iteration) if config.get('llm_provider') == 'anthropic' else None

        if new_model != current_model or current_model is None:
            current_model = new_model
            llm, code_analyst, bug_fixer, qa_verifier = create_crew_for_iteration(iteration, config)
            if not llm:
                log_error("Failed to create crew, skipping this bug")
                failures.pop(0)
                continue

        # Create dynamic tasks for this specific bug
        tasks = create_tasks_for_bug(
            failure, str(repo_path),
            code_analyst, bug_fixer, qa_verifier
        )

        # Assemble crew with tasks
        crew = Crew(
            agents=[code_analyst, bug_fixer, qa_verifier],
            tasks=tasks,
            process=Process.sequential,
            verbose=True,
            memory=False
        )

        try:
            # Kick off: PLAN → EXECUTE → OBSERVE
            log_info(f"[CREW] Starting PLAN -> EXECUTE -> OBSERVE for: {test_name}")
            result = crew.kickoff()

            log_info(f"[CREW] Result: {result}")

            # After crew finishes, re-run tests to get updated failure list
            log_info("Re-running tests after crew fix attempt...")
            test_output = run_tests(repo_path)
            new_results = parse_test_results(test_output, repo_path)

            # Check if this specific test now passes
            test_still_failing = any(
                f['test_name'] == test_name for f in new_results['failures']
            )

            if not test_still_failing:
                log_info(f"[OK] Successfully fixed {test_name}!")
                bugs_fixed += 1
            else:
                log_warning(f"[FAIL] Fix didn't work for {test_name}")

            # Update failures list with current state
            failures = new_results['failures']

        except Exception as e:
            log_error(f"CrewAI error for {test_name}: {e}")
            failures.pop(0)
            continue

    return bugs_fixed
