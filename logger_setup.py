"""
Logging setup - detailed tracking for each step
Simple functions, no classes!
"""

import logging
import sys
from datetime import datetime
from pathlib import Path


# Global logger instance
_logger = None
_log_file = None


def setup_logging(log_dir='./logs', log_level='INFO'):
    """
    Setup logging to both console and file
    
    Args:
        log_dir: Directory for log files
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR)
    """
    global _logger, _log_file
    
    # Create logs directory
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)
    
    # Create log file with timestamp
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    _log_file = log_path / f"bug_fixer_{timestamp}.log"
    
    # Setup logger
    _logger = logging.getLogger('BugFixer')
    _logger.setLevel(getattr(logging, log_level.upper()))
    
    # Console handler (with colors and UTF-8 encoding)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    
    # Set UTF-8 encoding for Windows console
    if sys.stdout.encoding != 'utf-8':
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    
    console_format = logging.Formatter(
        '%(asctime)s | %(levelname)s | %(message)s',
        datefmt='%H:%M:%S'
    )
    console_handler.setFormatter(console_format)
    
    # File handler (detailed)
    file_handler = logging.FileHandler(_log_file)
    file_handler.setLevel(logging.DEBUG)
    file_format = logging.Formatter(
        '%(asctime)s | %(levelname)-8s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    file_handler.setFormatter(file_format)
    
    # Add handlers
    _logger.addHandler(console_handler)
    _logger.addHandler(file_handler)
    
    _logger.info(f"Logging initialized - File: {_log_file}")


def log_info(message):
    """Log info message"""
    if _logger:
        _logger.info(message)
    else:
        print(f"INFO: {message}")


def log_debug(message):
    """Log debug message"""
    if _logger:
        _logger.debug(message)
    else:
        print(f"DEBUG: {message}")


def log_warning(message):
    """Log warning message"""
    if _logger:
        _logger.warning(message)
    else:
        print(f"WARNING: {message}")


def log_error(message):
    """Log error message"""
    if _logger:
        _logger.error(message)
    else:
        print(f"ERROR: {message}")


def log_test_run(test_name, status, details=""):
    """
    Log individual test execution
    
    Args:
        test_name: Name of the test
        status: PASS, FAIL, SKIP
        details: Additional details
    """
    # Windows-safe symbols
    status_symbol = {
        'PASS': '[PASS]',
        'FAIL': '[FAIL]',
        'SKIP': '[SKIP]'
    }.get(status, '[?]')
    
    log_info(f"{status_symbol} {test_name} - {status}")
    if details:
        log_debug(f"   Details: {details}")


def log_bug_fix_attempt(bug_number, total_bugs, test_name, approach):
    """
    Log a bug fix attempt
    
    Args:
        bug_number: Current bug number
        total_bugs: Total number of bugs
        test_name: Test that's failing
        approach: Fix approach being tried
    """
    log_info(f"\n{'='*60}")
    log_info(f"Bug {bug_number}/{total_bugs}: {test_name}")
    log_info(f"Approach: {approach}")
    log_info(f"{'='*60}")


def log_patch_applied(file_path, lines_changed):
    """
    Log when a patch is applied
    
    Args:
        file_path: File that was patched
        lines_changed: Number of lines changed
    """
    log_info(f"[OK] Patch applied: {file_path} ({lines_changed} lines changed)")


def log_cost_update(tokens_used, cost, total_cost, budget_remaining):
    """
    Log cost information
    
    Args:
        tokens_used: Tokens used in this call
        cost: Cost of this call
        total_cost: Running total cost
        budget_remaining: Remaining budget
    """
    log_debug(f"Tokens: {tokens_used:,} | Cost: ${cost:.4f} | "
              f"Total: ${total_cost:.4f} | Remaining: ${budget_remaining:.2f}")
    
    # Warn if approaching budget
    if budget_remaining < 1.0:
        log_warning(f"Budget running low: ${budget_remaining:.2f} remaining")


def get_log_file_path():
    """Get the path to the current log file"""
    return _log_file
