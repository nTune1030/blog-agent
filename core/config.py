"""
Configuration loader and environment path constants.

Includes structured JSON logging for machine-parseable log output.
Each log entry is a JSON object with timestamp, level, logger, message,
and optional context fields.
"""
import os
import json
import logging
import traceback
from datetime import datetime, timezone

# Base dir is one level up from 'core'
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")

# Default Config Structure
DEFAULT_CONFIG = {
  "agent": {
    "context_limits": {"local": 8192, "cloud": 32768}
  },
  "paths": {
    "sessions_base": "./sessions"
  },
  "hitl": {
    "enabled": True,
    "medium_requires_approval": False,
    "auto_approve_session": True,
    "approval_timeout_seconds": 300,
    "escalation_keywords": {
      "critical": [
        "rm ", "del ", "rmdir", "format", "mkfs", "dd ",
        "shutdown", "reboot", "passwd", "useradd",
        "chmod 777", "chown", ":(){ :|:& };:"
      ]
    },
    "sensitive_paths": [
      "/etc/passwd", "/etc/shadow", "~/.ssh", "~/.gnupg",
      "C:\\Windows\\System32"
    ],
    "risk_overrides": {}
  }
}

def load_config():
    """Load config.json from the project root, or create it with defaults if missing."""
    if not os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(DEFAULT_CONFIG, f, indent=2)
        return DEFAULT_CONFIG
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"[!] Errored loading config.json: {e}. Using defaults.")
        return DEFAULT_CONFIG

APP_CONFIG = load_config()

# Extract Paths
# Resolve relative paths relative to BASE_DIR
def resolve_path(p):
    """Resolve a potentially relative path against the project root directory."""
    if os.path.isabs(p): return p
    return os.path.join(BASE_DIR, os.path.normpath(p))

SESSIONS_BASE = resolve_path(APP_CONFIG.get("paths", {}).get("sessions_base", "./sessions"))

# Session specific directories
SESSION_ID = datetime.now().strftime('%Y%m%d_%H%M%S')
CURRENT_SESSION_DIR = os.path.join(SESSIONS_BASE, f"session_{SESSION_ID}")

LOGS_DIR = os.path.join(CURRENT_SESSION_DIR, "logs")
SCRIPTS_DIR = os.path.join(CURRENT_SESSION_DIR, "scripts")

def get_current_session_reports_dir() -> str:
    """Returns absolute path to the current session's reports directory, ensuring it exists."""
    reports_path = os.path.join(CURRENT_SESSION_DIR, "reports")
    os.makedirs(reports_path, exist_ok=True)
    return reports_path

# Global persistent memory directory
MEMORY_DIR = os.path.join(BASE_DIR, "memory")

os.makedirs(LOGS_DIR, exist_ok=True)
os.makedirs(SCRIPTS_DIR, exist_ok=True)
os.makedirs(MEMORY_DIR, exist_ok=True)

class Colors:
    """ANSI escape code constants for terminal output coloring."""
    GREEN, CYAN, YELLOW, RED, GRAY, RESET = '\033[92m', '\033[96m', '\033[93m', '\033[91m', '\033[90m', '\033[0m'


class StructuredJsonFormatter(logging.Formatter):
    """JSON log formatter for structured, machine-parseable log output.
    
    Each log entry is a JSON object with:
        - timestamp: ISO 8601 UTC timestamp
        - level: Log level (INFO, WARNING, ERROR, etc.)
        - logger: Logger name
        - message: Log message
        - module: Source module
        - function: Source function
        - line: Source line number
        - session_id: Current session identifier
    
    Extra fields passed via logger.info(..., extra={...}) are merged into
    the JSON object, enabling rich structured context for every log entry.
    """

    def __init__(self, session_id: str = ""):
        super().__init__()
        self.session_id = session_id

    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict[str, object] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
            "session_id": self.session_id,
        }
        # Merge any extra fields passed by the caller via extra={'extra_fields': {...}}
        extra = getattr(record, 'extra_fields', None)
        if extra and isinstance(extra, dict):
            log_entry.update(extra)
        # Include exception info if present
        if record.exc_info and record.exc_info[0] is not None:
            log_entry["exception"] = ''.join(
                traceback.format_exception(record.exc_info[0], record.exc_info[1], record.exc_info[2])
            )
        return json.dumps(log_entry, default=str)


# Logging Setup — structured JSON to file, human-readable to console
LOG_FILE = os.path.join(LOGS_DIR, "agent.log")

# File handler: structured JSON
file_handler = logging.FileHandler(LOG_FILE, encoding='utf-8')
file_handler.setLevel(logging.INFO)
file_handler.setFormatter(StructuredJsonFormatter(session_id=SESSION_ID))

# Console handler: human-readable for terminal
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.WARNING)
console_handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s'))

logger = logging.getLogger('BaseAgent')
logger.setLevel(logging.INFO)
logger.addHandler(file_handler)
logger.addHandler(console_handler)
# Prevent duplicate logs if root logger also has handlers
logger.propagate = False

os.system('')  # Enable ANSI colors on Windows
