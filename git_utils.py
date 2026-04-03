"""
Git utilities - handle both local repos and Git clones
Simple functions!
"""

import subprocess
import os
from pathlib import Path
import tempfile
import shutil
from logger_setup import log_info, log_error, log_debug


def setup_repository(repo_path_or_url):
    """
    Setup repository - handle both local paths and Git URLs
    
    Args:
        repo_path_or_url: Local path or Git URL
    
    Returns:
        Path: Path to the repository or None if failed
    """
    # Check if it's a Git URL
    if repo_path_or_url.startswith(('http://', 'https://', 'git@')):
        log_info(f"Detected Git URL: {repo_path_or_url}")
        return clone_repository(repo_path_or_url)
    else:
        # Local path
        repo_path = Path(repo_path_or_url)
        if not repo_path.exists():
            log_error(f"Repository path does not exist: {repo_path}")
            return None
        
        log_info(f"Using local repository: {repo_path}")
        
        # Initialize git if not already a repo
        if not (repo_path / '.git').exists():
            log_info("Initializing Git repository...")
            init_git(repo_path)
        
        return repo_path.resolve()


def clone_repository(git_url):
    """
    Clone a Git repository to a temporary directory
    
    Args:
        git_url: Git repository URL
    
    Returns:
        Path: Path to cloned repository or None if failed
    """
    try:
        # Create temp directory
        temp_dir = Path(tempfile.mkdtemp(prefix='bugfixer_'))
        log_info(f"Cloning to: {temp_dir}")
        
        # Clone
        result = subprocess.run(
            ['git', 'clone', git_url, str(temp_dir)],
            capture_output=True,
            text=True,
            timeout=300  # 5 minute timeout
        )
        
        if result.returncode != 0:
            log_error(f"Git clone failed: {result.stderr}")
            shutil.rmtree(temp_dir, ignore_errors=True)
            return None
        
        log_info(f"[OK] Repository cloned successfully")
        return temp_dir
        
    except subprocess.TimeoutExpired:
        log_error("Git clone timed out after 5 minutes")
        return None
    except Exception as e:
        log_error(f"Error cloning repository: {e}")
        return None


def init_git(repo_path):
    """
    Initialize a Git repository
    
    Args:
        repo_path: Path to repository
    
    Returns:
        bool: True if successful
    """
    try:
        # Init
        subprocess.run(['git', 'init'], cwd=repo_path, check=True, capture_output=True)
        
        # Configure
        subprocess.run(
            ['git', 'config', 'user.name', 'Bug Fixer Agent'],
            cwd=repo_path, check=True, capture_output=True
        )
        subprocess.run(
            ['git', 'config', 'user.email', 'bugfixer@agent.local'],
            cwd=repo_path, check=True, capture_output=True
        )
        
        # Initial commit
        subprocess.run(['git', 'add', '-A'], cwd=repo_path, check=True, capture_output=True)
        subprocess.run(
            ['git', 'commit', '-m', 'Initial commit', '--allow-empty'],
            cwd=repo_path, check=True, capture_output=True
        )
        
        log_info("[OK] Git repository initialized")
        return True
        
    except subprocess.CalledProcessError as e:
        log_error(f"Git init failed: {e}")
        return False


def create_snapshot(repo_path, message):
    """
    Create a Git snapshot (commit)
    
    Args:
        repo_path: Path to repository
        message: Commit message
    
    Returns:
        str: Commit hash or None if failed
    """
    try:
        # Stage all changes
        subprocess.run(
            ['git', 'add', '-A'],
            cwd=repo_path,
            check=True,
            capture_output=True
        )
        
        # Check if there are changes
        status = subprocess.run(
            ['git', 'status', '--porcelain'],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True
        )
        
        if not status.stdout.strip():
            # No changes, just return current HEAD
            return get_current_commit(repo_path)
        
        # Commit
        subprocess.run(
            ['git', 'commit', '-m', message],
            cwd=repo_path,
            check=True,
            capture_output=True
        )
        
        commit_hash = get_current_commit(repo_path)
        log_debug(f"Snapshot created: {commit_hash[:8]} - {message}")
        
        return commit_hash
        
    except subprocess.CalledProcessError as e:
        log_error(f"Failed to create snapshot: {e}")
        return None


def get_current_commit(repo_path):
    """
    Get current commit hash
    
    Args:
        repo_path: Path to repository
    
    Returns:
        str: Commit hash or empty string
    """
    try:
        result = subprocess.run(
            ['git', 'rev-parse', 'HEAD'],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True
        )
        return result.stdout.strip()
    except:
        return ""


def revert_to_snapshot(repo_path, commit_hash):
    """
    Revert repository to a specific commit
    
    Args:
        repo_path: Path to repository
        commit_hash: Commit to revert to
    
    Returns:
        bool: True if successful
    """
    try:
        subprocess.run(
            ['git', 'reset', '--hard', commit_hash],
            cwd=repo_path,
            check=True,
            capture_output=True
        )
        
        log_info(f"[OK] Reverted to snapshot: {commit_hash[:8]}")
        return True
        
    except subprocess.CalledProcessError as e:
        log_error(f"Failed to revert: {e}")
        return False


def get_diff(repo_path, file_path=None):
    """
    Get diff of changes
    
    Args:
        repo_path: Path to repository
        file_path: Specific file (optional)
    
    Returns:
        str: Diff output
    """
    try:
        cmd = ['git', 'diff']
        if file_path:
            cmd.append(file_path)
        
        result = subprocess.run(
            cmd,
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True
        )
        return result.stdout
    except:
        return ""
