#!/usr/bin/env python3
"""
Diagnostic script to debug pytest issues
Run this to see what's happening with your tests
"""

import subprocess
import sys
from pathlib import Path
import json

def check_environment():
    """Check Python environment and dependencies"""
    print("=" * 80)
    print("ENVIRONMENT CHECK")
    print("=" * 80)
    
    # Python version
    print(f"\nPython version: {sys.version}")
    
    # Check pytest
    try:
        result = subprocess.run(['python', '-m', 'pytest', '--version'], 
                              capture_output=True, text=True)
        print(f"Pytest version: {result.stdout.strip()}")
    except:
        print("ERROR: pytest not installed or not working")
        print("Install with: pip install pytest")
        return False
    
    # Check pytest-json-report
    result = subprocess.run(['python', '-m', 'pytest', '--help'], 
                          capture_output=True, text=True)
    if '--json-report' in result.stdout:
        print("✓ pytest-json-report is installed")
    else:
        print("⚠ pytest-json-report NOT installed")
        print("  This is optional but recommended")
        print("  Install with: pip install pytest-json-report")
    
    return True


def check_test_files(repo_path):
    """Check what test files exist"""
    print("\n" + "=" * 80)
    print("TEST FILES CHECK")
    print("=" * 80)
    
    repo_path = Path(repo_path)
    print(f"\nSearching in: {repo_path.absolute()}")
    print(f"Directory exists: {repo_path.exists()}")
    
    # Look for test files
    test_files = list(repo_path.glob("test_*.py")) + list(repo_path.glob("*_test.py"))
    
    if test_files:
        print(f"\n✓ Found {len(test_files)} test file(s):")
        for f in test_files:
            print(f"  - {f.name} ({f.stat().st_size} bytes)")
            
            # Quick syntax check
            try:
                with open(f, 'r') as file:
                    compile(file.read(), f.name, 'exec')
                print(f"    ✓ Syntax OK")
            except SyntaxError as e:
                print(f"    ✗ SYNTAX ERROR: {e}")
    else:
        print("\n✗ NO TEST FILES FOUND")
        print("  Expected files matching: test_*.py or *_test.py")
        
        # Show all Python files
        all_py = list(repo_path.glob("*.py"))
        if all_py:
            print(f"\n  Other Python files found:")
            for f in all_py:
                print(f"    - {f.name}")
    
    return test_files


def run_pytest_verbose(repo_path):
    """Run pytest with maximum verbosity"""
    print("\n" + "=" * 80)
    print("PYTEST EXECUTION (VERY VERBOSE)")
    print("=" * 80)
    
    cmd = [
        'python', '-m', 'pytest',
        '-vv',                # Extra verbose
        '--collect-only',     # Just show what would be collected
        '--tb=short',
        '--color=no',
        '.'
    ]
    
    print(f"\nCommand: {' '.join(cmd)}")
    print(f"Working directory: {repo_path}\n")
    
    result = subprocess.run(
        cmd,
        cwd=str(repo_path),
        capture_output=True,
        text=True
    )
    
    print("STDOUT:")
    print(result.stdout)
    print("\nSTDERR:")
    print(result.stderr)
    print(f"\nExit code: {result.returncode}")
    
    return result


def run_pytest_actual(repo_path):
    """Run pytest for real"""
    print("\n" + "=" * 80)
    print("PYTEST EXECUTION (ACTUAL RUN)")
    print("=" * 80)
    
    cmd = [
        'python', '-m', 'pytest',
        '-v',
        '--tb=short',
        '--color=no',
        '.'
    ]
    
    print(f"\nCommand: {' '.join(cmd)}")
    print(f"Working directory: {repo_path}\n")
    
    result = subprocess.run(
        cmd,
        cwd=str(repo_path),
        capture_output=True,
        text=True
    )
    
    output = result.stdout + "\n" + result.stderr
    print(output)
    
    return output


def analyze_output(output):
    """Analyze pytest output"""
    print("\n" + "=" * 80)
    print("OUTPUT ANALYSIS")
    print("=" * 80)
    
    import re
    
    # Check for collection
    if "collected" in output.lower():
        match = re.search(r'collected (\d+) items?', output, re.IGNORECASE)
        if match:
            print(f"\n✓ Pytest collected {match.group(1)} test(s)")
        else:
            print("\n⚠ 'collected' found but couldn't parse count")
    else:
        print("\n✗ No 'collected' message - tests may not have been found")
    
    # Check for errors
    errors = []
    if "ImportError" in output:
        errors.append("ImportError detected")
    if "ModuleNotFoundError" in output:
        errors.append("ModuleNotFoundError detected")
    if "SyntaxError" in output:
        errors.append("SyntaxError detected")
    if "ERROR" in output and "collected" not in output:
        errors.append("Generic ERROR detected")
    
    if errors:
        print("\n✗ Issues found:")
        for err in errors:
            print(f"  - {err}")
    else:
        print("\n✓ No obvious errors detected")
    
    # Check for results
    patterns = [
        (r'(\d+) passed', 'passed'),
        (r'(\d+) failed', 'failed'),
        (r'(\d+) skipped', 'skipped'),
    ]
    
    print("\nTest results:")
    found_any = False
    for pattern, name in patterns:
        match = re.search(pattern, output)
        if match:
            print(f"  {name}: {match.group(1)}")
            found_any = True
    
    if not found_any:
        print("  ✗ No test results found in output")


def main():
    """Main diagnostic function"""
    print("""
╔════════════════════════════════════════════════════════════╗
║           PYTEST DIAGNOSTIC TOOL                           ║
║  This script helps debug why pytest finds 0 tests         ║
╚════════════════════════════════════════════════════════════╝
""")
    
    # Get repository path
    if len(sys.argv) > 1:
        repo_path = sys.argv[1]
    else:
        repo_path = input("Enter path to repository (or '.' for current): ").strip()
        if not repo_path:
            repo_path = "."
    
    repo_path = Path(repo_path).absolute()
    
    # Run diagnostics
    if not check_environment():
        print("\nFix environment issues first!")
        return
    
    test_files = check_test_files(repo_path)
    
    if not test_files:
        print("\n" + "=" * 80)
        print("DIAGNOSIS: No test files found")
        print("=" * 80)
        print("\nPossible solutions:")
        print("1. Make sure you're in the right directory")
        print("2. Test files must start with 'test_' or end with '_test.py'")
        print("3. Test functions inside must also start with 'test_'")
        return
    
    # Run pytest in collection mode
    run_pytest_verbose(repo_path)
    
    # Run pytest for real
    output = run_pytest_actual(repo_path)
    
    # Analyze
    analyze_output(output)
    
    print("\n" + "=" * 80)
    print("DIAGNOSTIC COMPLETE")
    print("=" * 80)
    print("\nCommon issues:")
    print("1. Test files exist but pytest can't import them (ImportError)")
    print("2. Test files have no test functions (forgot 'def test_' prefix)")
    print("3. Tests are in subdirectories and pytest isn't configured to find them")
    print("4. __init__.py missing in package directories")
    print("5. Circular import issues")


if __name__ == "__main__":
    main()
