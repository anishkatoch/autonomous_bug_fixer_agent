"""
Test Utilities - Run pytest and parse results
All functions are simple and well-documented!

Functions:
- run_tests(): Execute pytest in repository
- parse_test_results(): Extract failures from pytest output
- parse_json_report(): Parse pytest JSON report
- parse_text_output(): Fallback text parser
"""

import subprocess
import json
import sys
from pathlib import Path
from logger_setup import log_info, log_debug, log_error, log_test_run, log_warning


def run_tests(repo_path):
    """
    Run pytest test suite in the repository

    What it does:
    1. Executes pytest with verbose output
    2. Generates JSON report for easy parsing (if plugin available)
    3. Captures both stdout and stderr
    4. Returns combined output

    Args:
        repo_path: Path to repository containing tests

    Returns:
        str: Complete pytest output (stdout + stderr)
    """
    log_info("Running pytest test suite...")

    # Convert to Path object
    repo_path = Path(repo_path)

    # First, check what test files exist
    log_info(f"Searching for test files in: {repo_path}")
    test_files = list(repo_path.glob("test_*.py")) + list(repo_path.glob("*_test.py"))

    if test_files:
        log_info(f"Found {len(test_files)} test file(s):")
        for test_file in test_files:
            log_info(f"  - {test_file.name}")
    else:
        log_error("NO TEST FILES FOUND!")
        log_error(f"Looked in: {repo_path}")
        log_error("Expected files matching: test_*.py or *_test.py")
        return "ERROR: No test files found in repository"

    # Check if pytest-json-report is available
    try:
        # Use sys.executable to ensure we use the correct Python environment
        result_check = subprocess.run(
            [sys.executable, '-m', 'pytest', '--help'],
            capture_output=True,
            text=True,
            timeout=10,
            encoding='utf-8',
            errors='replace'
        )
        has_json_report = '--json-report' in result_check.stdout
    except Exception as e:
        log_debug(f"Error checking for pytest-json-report: {e}")
        has_json_report = False

    # Build pytest command
    cmd = [
        sys.executable,      # Use current Python interpreter
        '-m', 'pytest',      # Run pytest as a module
        '-v',                # Verbose output
        '--tb=short',        # Short traceback format
        '--color=no',        # No ANSI colors in output
    ]

    # Add JSON report if available
    if has_json_report:
        cmd.extend(['--json-report', '--json-report-file=.test_results.json'])
        log_debug("Using pytest-json-report for structured output")
    else:
        log_warning("pytest-json-report not available, will use text parsing")
        log_warning("Install with: pip install pytest-json-report")

    cmd.append('.')  # Run tests in current directory

    try:
        # Run pytest IN the repository directory
        log_debug(f"Executing: {' '.join(cmd)} in {repo_path}")
        result = subprocess.run(
            cmd,
            cwd=str(repo_path),   # CRITICAL: Run in repo directory
            capture_output=True,
            text=True,
            timeout=300,          # 5 minute timeout
            encoding='utf-8',     # CRITICAL: Force UTF-8 encoding for Windows
            errors='replace'      # CRITICAL: Replace decode errors instead of crashing
        )

        output = result.stdout + "\n" + result.stderr
        log_debug(f"Pytest exit code: {result.returncode}")
        log_debug(f"Output length: {len(output)} chars")

        # Log first 1000 chars of output for debugging
        log_debug("Pytest output preview:")
        preview = output[:1000] if len(output) > 1000 else output
        log_debug(preview)

        # Show what was collected
        if "collected" in output.lower():
            import re
            match = re.search(r'collected (\d+) items?', output, re.IGNORECASE)
            if match:
                count = match.group(1)
                log_info(f"Pytest collected {count} test(s)")
            else:
                log_warning("Pytest ran but didn't report collection count")
        else:
            log_warning("No 'collected' message in pytest output - may have errors")
            log_warning("Full pytest output:")
            log_warning(output)

            # Check for common errors
            if "ImportError" in output or "ModuleNotFoundError" in output:
                log_error("Import errors detected in test files!")
                # Show which imports failed
                import re
                for line in output.split('\n'):
                    if 'ImportError' in line or 'ModuleNotFoundError' in line:
                        log_error(f"  {line.strip()}")
            if "SyntaxError" in output:
                log_error("Syntax errors detected in test files!")
            if "No module named" in output:
                log_error("Missing module - check your dependencies!")
            if result.returncode != 0 and len(output.strip()) < 50:
                log_error("Pytest produced very little output - it may not be installed correctly")
                log_error("Try: pip install pytest")

        return output

    except subprocess.TimeoutExpired:
        log_error("Pytest timed out after 5 minutes")
        return "ERROR: Test execution timeout"
    except Exception as e:
        log_error(f"Error running tests: {e}")
        import traceback
        log_debug(traceback.format_exc())
        return f"ERROR: {str(e)}"


def parse_test_results(test_output, repo_path=None):
    """
    Parse pytest output to extract test results

    What it does:
    1. First tries to read JSON report (most reliable)
    2. Falls back to text parsing if JSON not available
    3. Extracts: total, passed, failed, skipped counts
    4. Gets detailed failure information
    5. Logs each test result

    Args:
        test_output: Raw pytest output string
        repo_path: Path to repo (to find JSON report)

    Returns:
        dict: {
            'total': int,
            'passed': int,
            'failed': int,
            'skipped': int,
            'failures': [
                {
                    'test_name': str,
                    'test_file': str,
                    'error_message': str,
                    'traceback': str
                },
                ...
            ],
            'passing_tests': [str, ...]
        }
    """
    log_debug("Parsing test results...")

    # Try JSON report first (most reliable)
    if repo_path:
        json_file = Path(repo_path) / '.test_results.json'
        if json_file.exists():
            try:
                results = parse_json_report(json_file)
                json_file.unlink()  # Clean up
                log_info(f"Parsed {results['total']} tests from JSON report")
                return results
            except Exception as e:
                log_debug(f"JSON parsing failed: {e}, falling back to text parsing")
        else:
            log_debug("JSON report not found, using text parsing")

    # Fallback to text parsing
    results = parse_text_output(test_output)

    if results['total'] == 0:
        log_warning("Parsed 0 tests! This might indicate:")
        log_warning("  - Pytest didn't run successfully")
        log_warning("  - Tests have import errors")
        log_warning("  - Output parsing failed")
        log_warning("  - No test functions found (forgot 'test_' prefix?)")
        log_debug(f"Raw output (first 2000 chars): {test_output[:2000]}")

        # Additional diagnostic
        if "ERROR" in test_output or "FAILED" in test_output:
            log_error("Pytest encountered errors during collection/execution")
        if len(test_output.strip()) < 50:
            log_error("Pytest output is suspiciously short - may not have run at all")
    else:
        log_info(f"Parsed {results['total']} tests from text output")

    return results


def parse_json_report(json_file):
    """
    Parse pytest JSON report file

    What it does:
    1. Loads JSON report generated by pytest
    2. Extracts summary statistics
    3. Processes each test result
    4. Separates passing and failing tests
    5. Extracts error details for failures

    Args:
        json_file: Path to .test_results.json

    Returns:
        dict: Structured test results
    """
    with open(json_file, 'r', encoding='utf-8') as f:
        data = json.load(f)

    summary = data.get('summary', {})
    tests = data.get('tests', [])

    results = {
        'total': summary.get('total', 0),
        'passed': summary.get('passed', 0),
        'failed': summary.get('failed', 0),
        'skipped': summary.get('skipped', 0),
        'failures': [],
        'passing_tests': []
    }

    # Process each test
    for test in tests:
        test_id = test.get('nodeid', '')
        outcome = test.get('outcome', '')

        # Log individual test
        log_test_run(test_id, outcome.upper())

        if outcome == 'passed':
            results['passing_tests'].append(test_id)

        elif outcome == 'failed':
            # Extract failure details
            call_info = test.get('call', {})
            longrepr = call_info.get('longrepr', '')

            # Parse traceback
            traceback = longrepr if isinstance(longrepr, str) else str(longrepr)

            # Extract error message (usually the last line)
            error_lines = traceback.strip().split('\n')
            error_message = error_lines[-1] if error_lines else 'Unknown error'

            failure = {
                'test_name': test_id,
                'test_file': test_id.split('::')[0] if '::' in test_id else '',
                'error_message': error_message,
                'traceback': traceback
            }

            results['failures'].append(failure)

    return results


def parse_text_output(output):
    """
    Fallback parser for pytest text output

    What it does:
    1. Uses regex to find test summary line
    2. Extracts pass/fail/skip counts
    3. Finds FAILED and PASSED test lines
    4. Extracts basic error information

    Args:
        output: Pytest stdout/stderr text

    Returns:
        dict: Basic test results (less detailed than JSON)
    """
    import re

    results = {
        'total': 0,
        'passed': 0,
        'failed': 0,
        'skipped': 0,
        'failures': [],
        'passing_tests': []
    }

    # Find summary line - multiple possible formats:
    # "7 failed, 13 passed in 1.23s"
    # "= 13 passed in 1.23s ="
    # "= 7 failed, 13 passed, 2 skipped in 1.23s ="
    
    patterns = [
        r'=+\s*(\d+)\s+failed(?:,\s*(\d+)\s+passed)?(?:,\s*(\d+)\s+skipped)?',
        r'(\d+)\s+failed(?:,\s*(\d+)\s+passed)?(?:,\s*(\d+)\s+skipped)?',
        r'=+\s*(\d+)\s+passed(?:,\s*(\d+)\s+failed)?(?:,\s*(\d+)\s+skipped)?',
        r'(\d+)\s+passed(?:,\s*(\d+)\s+failed)?(?:,\s*(\d+)\s+skipped)?',
    ]

    for pattern in patterns:
        match = re.search(pattern, output)
        if match:
            groups = match.groups()
            
            # Determine which number is which based on pattern
            if 'failed' in pattern[:20]:  # First group is failures
                results['failed'] = int(groups[0])
                results['passed'] = int(groups[1]) if groups[1] else 0
                results['skipped'] = int(groups[2]) if groups[2] else 0
            else:  # First group is passes
                results['passed'] = int(groups[0])
                results['failed'] = int(groups[1]) if groups[1] else 0
                results['skipped'] = int(groups[2]) if groups[2] else 0

            results['total'] = results['passed'] + results['failed'] + results['skipped']
            log_debug(f"Matched pattern: {pattern}")
            log_debug(f"Counts: passed={results['passed']}, failed={results['failed']}, skipped={results['skipped']}")
            break

    # Find FAILED tests
    # Example: "test_inventory_system.py::test_bug_1_negative_price_allowed FAILED"
    failed_pattern = r'([\w/\.]+::\w+)\s+FAILED'
    for match in re.finditer(failed_pattern, output):
        test_name = match.group(1)

        # Try to find error message for this test
        error_msg = "Test failed - see full output"

        results['failures'].append({
            'test_name': test_name,
            'test_file': test_name.split('::')[0],
            'error_message': error_msg,
            'traceback': f"See pytest output for {test_name}"
        })

    # Find PASSED tests
    passed_pattern = r'([\w/\.]+::\w+)\s+PASSED'
    for match in re.finditer(passed_pattern, output):
        results['passing_tests'].append(match.group(1))
    
    # Validate: if we found individual tests, update counts if they were zero
    if results['total'] == 0:
        found_failed = len(results['failures'])
        found_passed = len(results['passing_tests'])
        if found_failed > 0 or found_passed > 0:
            log_debug(f"Summary line not found, but found {found_passed} PASSED and {found_failed} FAILED markers")
            results['passed'] = found_passed
            results['failed'] = found_failed
            results['total'] = found_passed + found_failed
    
    return results


def format_failure_for_agent(failure):
    """
    Format a test failure for CrewAI agent consumption
    
    What it does:
    1. Takes raw failure data
    2. Formats it into readable text
    3. Highlights key information
    4. Makes it easy for LLM to understand
    
    Args:
        failure: Failure dict from parse_test_results
    
    Returns:
        str: Formatted failure description
    """
    formatted = f"""
TEST FAILURE: {failure['test_name']}
{'='*60}

File: {failure['test_file']}

Error Message:
{failure['error_message']}

Full Traceback:
{failure['traceback']}

{'='*60}
"""
    return formatted.strip()
