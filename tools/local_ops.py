"""
Local system execution tools.
"""
import os
import subprocess
from core.config import SCRIPTS_DIR, LOGS_DIR

def execute_command(cmd: str, cwd: str = None, timeout: int = 60) -> str:
    """Run local Windows command"""
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, cwd=cwd, timeout=timeout)
        return result.stdout + result.stderr
    except subprocess.TimeoutExpired as e:
        out = (e.stdout if isinstance(e.stdout, str) else (e.stdout.decode('utf-8', errors='ignore') if e.stdout else ""))
        err = (e.stderr if isinstance(e.stderr, str) else (e.stderr.decode('utf-8', errors='ignore') if e.stderr else ""))
        return f"[TIMEOUT] Command exceeded {timeout}s: {cmd}\nOutput before timeout:\n{out}{err}"
    except Exception as e:
        return f"[ERROR] {e}"

def save_to_local_file(content: str, filename: str) -> str:
    """Save files/scripts to the scripts folder"""
    filepath = os.path.join(SCRIPTS_DIR, filename)
    try:
        with open(filepath, 'w') as f:
            f.write(content)
        return f"[Saved to {filepath}]"
    except Exception as e:
        return f"[ERROR saving file: {e}]"

def cat_local_file(filename: str) -> str:
    """Read a saved file. Supports simple filenames (searched in current session scripts/logs), cross-session paths (sessions/...), and absolute paths."""
    # Absolute path — use directly
    if os.path.isabs(filename):
        filepath = filename
    # Cross-session or relative project path — resolve from project root
    elif 'sessions' in filename or '..' in filename:
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        filepath = os.path.join(project_root, filename)
    else:
        # Simple filename — check current session scripts/ then logs/
        filepath = os.path.join(SCRIPTS_DIR, filename)
        if not os.path.exists(filepath):
            filepath = os.path.join(LOGS_DIR, filename)
    try:
        with open(filepath, 'r') as f:
            return f.read()
    except Exception as e:
        return f"[ERROR reading file: {e}]"

def grep_local_file(pattern: str, filename: str) -> str:
    """Grep pattern in file"""
    filepath = os.path.join(SCRIPTS_DIR, filename)
    if not os.path.exists(filepath):
        filepath = os.path.join(LOGS_DIR, filename)
    try:
        with open(filepath, 'r') as f:
            lines = f.readlines()
        matches = [line for line in lines if pattern in line]
        return "".join(matches) if matches else "[No matches found]"
    except Exception as e:
        return f"[ERROR grepping file: {e}]"
