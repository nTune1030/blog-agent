"""
Human-In-The-Loop (HITL) Authorization System.

Gates dangerous tool executions based on risk classification.
The agent retains full capability — HITL ensures a human approves
before risky operations execute. No sandboxing, no blocking.
The human is the safety layer.

Features:
- Risk classification (LOW/MEDIUM/HIGH/CRITICAL) with argument-level escalation
- Session-based approval caching
- Configurable approval timeout (auto-rejects if operator doesn't respond)
- Full audit trail via telemetry
"""
import json
import hashlib
import threading
import sys
from enum import Enum
from dataclasses import dataclass, field
from typing import Any, Callable, Optional
from core.config import Colors, logger


class RiskLevel(Enum):
    """Risk classification levels for tool executions."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class HITLDecision:
    """Result of evaluating a tool call through the HITL system."""
    action: str          # "auto_approve" or "require_approval" — never "blocked"
    risk_level: str      # "low", "medium", "high", "critical"
    reason: str          # Human-readable explanation
    escalated: bool = False  # Whether args caused escalation from base risk


# ─── Escalation Rules ───────────────────────────────────────────────
# These rules promote risk level based on argument content.
# They do NOT block — they only require human approval first.

DEFAULT_ESCALATION_RULES = {
    "execute_command": {
        "keywords": [
            "rm ", "del ", "rmdir", "format", "mkfs", "dd ",
            "shutdown", "reboot", ":(){ :|:& };:",
            "chmod 777", "chown", "passwd", "useradd", "usermod"
        ],
        "escalate_to": "critical"
    },
    "install_python_package": {
        "always_escalate_to": "high"
    },
    "fetch_web_content": {
        "argument_rules": {
            "verify": False,
            "escalate_to": "high"
        }
    },
    "cat_local_file": {
        "sensitive_paths": [
            "/etc/passwd", "/etc/shadow", "~/.ssh", "~/.gnupg",
            "C:\\Windows\\System32"
        ],
        "escalate_to": "critical"
    }
}

# ─── Default Risk Classifications ───────────────────────────────────
# Applied when a tool doesn't declare its own risk via @hitl_risk

DEFAULT_RISK_CLASSIFICATIONS = {
    "smart_search_learnings": RiskLevel.LOW,
    "record_learning": RiskLevel.MEDIUM,
    "save_note": RiskLevel.MEDIUM,
    "report_finding": RiskLevel.MEDIUM,
    "cat_local_file": RiskLevel.LOW,
    "grep_local_file": RiskLevel.LOW,
    "save_to_local_file": RiskLevel.MEDIUM,
    "execute_command": RiskLevel.HIGH,
    "install_python_package": RiskLevel.HIGH,
    "fetch_web_content": RiskLevel.MEDIUM,
    "fuzz_web_endpoint": RiskLevel.CRITICAL,
    "reset_web_session": RiskLevel.LOW,
    "seed_knowledge_base": RiskLevel.MEDIUM,
    "search_github": RiskLevel.LOW,
    "search_stackoverflow": RiskLevel.LOW,
    "deep_research": RiskLevel.LOW,
    "validate_and_enhance_command": RiskLevel.MEDIUM,
    "generate_vuln_report": RiskLevel.MEDIUM,
    "render_promotion_post": RiskLevel.LOW,
    "preview_all_platforms": RiskLevel.LOW,
    "save_promotion_content": RiskLevel.MEDIUM,
    "update_promotion_profile": RiskLevel.MEDIUM,
    "get_promotion_tips": RiskLevel.LOW,
    "post_to_reddit": RiskLevel.CRITICAL,
    "post_to_twitter": RiskLevel.CRITICAL,
    "post_to_discord": RiskLevel.CRITICAL,
    "post_to_all_platforms": RiskLevel.CRITICAL,
    "check_posting_readiness": RiskLevel.LOW,
}


def hitl_risk(risk_level: RiskLevel):
    """Decorator to declare a tool function's risk level.
    
    Usage:
        @hitl_risk(RiskLevel.HIGH)
        def execute_command(cmd: str, cwd: str = None, timeout: int = 60) -> str:
            ...
    """
    def decorator(func: Callable) -> Callable:
        func._hitl_risk = risk_level
        return func
    return decorator


def classify_default_risk(func: Callable) -> RiskLevel:
    """Heuristic fallback to classify a tool's risk level based on its name and docstring."""
    name = func.__name__.lower()
    doc = (func.__doc__ or "").lower()
    
    # Check the default classifications table first
    if name in DEFAULT_RISK_CLASSIFICATIONS:
        return DEFAULT_RISK_CLASSIFICATIONS[name]
    
    # Heuristic: keywords in function name or docstring
    critical_keywords = ["fuzz", "exploit", "attack", "inject", "delete", "destroy", "wipe"]
    high_keywords = ["execute", "run", "command", "shell", "install", "pip", "sudo", "admin", "root"]
    medium_keywords = ["write", "save", "create", "modify", "update", "send", "post", "seed"]
    
    for kw in critical_keywords:
        if kw in name or kw in doc:
            return RiskLevel.CRITICAL
    for kw in high_keywords:
        if kw in name or kw in doc:
            return RiskLevel.HIGH
    for kw in medium_keywords:
        if kw in name or kw in doc:
            return RiskLevel.MEDIUM
    
    return RiskLevel.LOW


def _timed_input(prompt: str, timeout_seconds: int) -> str | None:
    """Read input with a timeout. Returns None if timeout expires.
    
    Uses a background thread to implement the timeout since signal.alarm
    is not available on Windows. If the operator doesn't respond within
    the timeout, the input is auto-rejected.
    """
    result: list[str | None] = [None]
    
    def _read():
        try:
            result[0] = input(prompt)
        except (KeyboardInterrupt, EOFError):
            result[0] = None
    
    reader = threading.Thread(target=_read, daemon=True)
    reader.start()
    reader.join(timeout=timeout_seconds)
    
    if reader.is_alive():
        # Timeout expired — reader thread is still blocking on input
        print(f"\n{Colors.RED}[⏱] Approval timeout expired ({timeout_seconds}s). Auto-rejecting.{Colors.RESET}")
        logger.warning(f"HITL approval timeout expired after {timeout_seconds}s — auto-rejecting")
        return None
    
    return result[0]


class HITLAuthorizer:
    """Human-In-The-Loop authorization gate for tool executions.
    
    The agent retains full capability. HITL does NOT sandbox, restrict,
    or block capabilities — it ensures a human approves before dangerous
    operations execute. The human is the safety layer.
    
    Approval timeout: If the operator doesn't respond within the configured
    timeout (default 300s / 5 min), the request is auto-rejected.
    """
    
    def __init__(self, config: dict):
        self.risk_registry: dict[str, RiskLevel] = {}
        self.approval_cache: dict[str, str] = {}  # args_signature -> "approved" | "rejected"
        self.config = config.get("hitl", {})
        self.enabled = self.config.get("enabled", True)
        self.medium_requires_approval = self.config.get("medium_requires_approval", False)
        self.auto_approve_session = self.config.get("auto_approve_session", True)
        self.approval_timeout_seconds: int = self.config.get("approval_timeout_seconds", 300)
        self.session_approvals: list[dict] = []  # Audit log
        self.escalation_rules = DEFAULT_ESCALATION_RULES.copy()
        self.risk_overrides = self.config.get("risk_overrides", {})
        
        # Load escalation keywords from config (allows operator customization)
        config_keywords = self.config.get("escalation_keywords", {})
        if "critical" in config_keywords:
            if "execute_command" in self.escalation_rules:
                self.escalation_rules["execute_command"]["keywords"] = config_keywords["critical"]
        
        # Load sensitive paths from config
        config_paths = self.config.get("sensitive_paths", [])
        if config_paths and "cat_local_file" in self.escalation_rules:
            self.escalation_rules["cat_local_file"]["sensitive_paths"] = config_paths
    
    def register_risk(self, tool_name: str, risk_level: RiskLevel) -> None:
        """Register a tool's risk level. Called during plugin loading."""
        # Config overrides take precedence
        if tool_name in self.risk_overrides:
            override = self.risk_overrides[tool_name]
            try:
                risk_level = RiskLevel(override)
            except ValueError:
                logger.warning(f"Invalid risk override '{override}' for tool '{tool_name}', using default.")
        
        self.risk_registry[tool_name] = risk_level
        logger.info(f"HITL: Registered '{tool_name}' as {risk_level.value}")
    
    def evaluate(self, tool_name: str, args: dict) -> HITLDecision:
        """Evaluate a tool call and return a HITL decision.
        
        Checks:
        1. Is HITL enabled?
        2. What's the base risk level for this tool?
        3. Do the arguments escalate the risk level?
        4. Has this exact tool+args combo been approved before this session?
        
        Returns AUTO_APPROVE or REQUIRE_APPROVAL — never "blocked".
        """
        if not self.enabled:
            return HITLDecision(
                action="auto_approve",
                risk_level="low",
                reason="HITL is disabled in config",
                escalated=False
            )
        
        # Get base risk level
        base_risk = self.risk_registry.get(tool_name, RiskLevel.LOW)
        current_risk = base_risk
        escalated = False
        reason = f"Tool '{tool_name}' classified as {base_risk.value}"
        
        # Check argument-level escalation
        escalation = self._check_escalation(tool_name, args)
        if escalation:
            current_risk = RiskLevel(escalation["escalate_to"])
            escalated = True
            reason = escalation["reason"]
        
        # Check if this exact tool+args was already approved this session
        if self.auto_approve_session:
            sig = self._args_signature(tool_name, args)
            if sig in self.approval_cache:
                cached = self.approval_cache[sig]
                if cached == "approved":
                    return HITLDecision(
                        action="auto_approve",
                        risk_level=current_risk.value,
                        reason=f"Previously approved this session ({reason})",
                        escalated=escalated
                    )
        
        # Determine action based on risk level
        if current_risk == RiskLevel.LOW:
            return HITLDecision(
                action="auto_approve",
                risk_level=current_risk.value,
                reason=reason,
                escalated=escalated
            )
        elif current_risk == RiskLevel.MEDIUM:
            if self.medium_requires_approval:
                return HITLDecision(
                    action="require_approval",
                    risk_level=current_risk.value,
                    reason=reason,
                    escalated=escalated
                )
            return HITLDecision(
                action="auto_approve",
                risk_level=current_risk.value,
                reason=reason,
                escalated=escalated
            )
        else:
            # HIGH or CRITICAL — always require approval
            return HITLDecision(
                action="require_approval",
                risk_level=current_risk.value,
                reason=reason,
                escalated=escalated
            )
    
    def request_approval(self, tool_name: str, args: dict, risk_level: str) -> dict:
        """Interactive CLI prompt for operator approval with configurable timeout.
        
        If the operator doesn't respond within `approval_timeout_seconds`,
        the request is automatically rejected.
        
        Returns dict with:
            'decision': 'approve' | 'reject' | 'redirect'
            'args': possibly modified args dict (if redirect)
        """
        # Format args for display
        args_display = json.dumps(args, default=str, indent=2)
        if len(args_display) > 500:
            args_display = args_display[:500] + "\n  ...[truncated]"
        
        timeout = self.approval_timeout_seconds
        timeout_hint = f"  {Colors.GRAY}(Auto-reject in {timeout}s if no response){Colors.RESET}"
        
        if risk_level == "critical":
            # CRITICAL: require explicit YES confirmation
            print(f"\n{Colors.RED}{'=' * 60}")
            print(f"🚨 CRITICAL RISK — DESTRUCTIVE OPERATION 🚨")
            print(f"{'=' * 60}{Colors.RESET}")
            print(f"  {Colors.YELLOW}Tool:{Colors.RESET}     {tool_name}")
            print(f"  {Colors.YELLOW}Risk:{Colors.RESET}     {Colors.RED}{risk_level.upper()}{Colors.RESET}")
            print(f"  {Colors.YELLOW}Reason:{Colors.RESET}   {self._get_risk_reason(tool_name, risk_level)}")
            print(f"  {Colors.YELLOW}Args:{Colors.RESET}")
            for line in args_display.split('\n'):
                print(f"    {line}")
            print(f"\n  {Colors.RED}⚠️  This operation could cause significant damage.{Colors.RESET}")
            print(f"  {Colors.YELLOW}The agent can do anything — but are you sure you want to approve this?{Colors.RESET}")
            print(timeout_hint)
            print(f"\n  Type {Colors.GREEN}YES{Colors.RESET} to approve, or anything else to reject:")
            
            response = _timed_input(f"  {Colors.CYAN}> {Colors.RESET}", timeout)
            
            if response is None:
                # Timeout expired
                self._record_decision(tool_name, args, "timeout_rejected", risk_level)
                return {'decision': 'reject', 'args': args}
            
            if response == "YES":
                self._record_decision(tool_name, args, "approved", risk_level)
                return {'decision': 'approve', 'args': args}
            else:
                self._record_decision(tool_name, args, "rejected", risk_level)
                return {'decision': 'reject', 'args': args}
        
        else:
            # HIGH or MEDIUM: standard approval prompt
            print(f"\n{Colors.YELLOW}{'=' * 60}")
            print(f"⚠️  HITL APPROVAL REQUIRED — {risk_level.upper()} RISK")
            print(f"{'=' * 60}{Colors.RESET}")
            print(f"  {Colors.CYAN}Tool:{Colors.RESET}     {tool_name}")
            print(f"  {Colors.CYAN}Risk:{Colors.RESET}     {Colors.YELLOW}{risk_level.upper()}{Colors.RESET}")
            print(f"  {Colors.CYAN}Reason:{Colors.RESET}   {self._get_risk_reason(tool_name, risk_level)}")
            print(f"  {Colors.CYAN}Args:{Colors.RESET}")
            for line in args_display.split('\n'):
                print(f"    {line}")
            print(timeout_hint)
            print(f"\n  {Colors.GREEN}[a]{Colors.RESET} Approve  {Colors.RED}[r]{Colors.RESET} Reject  {Colors.CYAN}[m]{Colors.RESET} Modify args  {Colors.YELLOW}[s]{Colors.RESET} Approve for session")
            
            response = _timed_input(f"  {Colors.CYAN}> {Colors.RESET}", timeout)
            
            if response is None:
                # Timeout expired
                self._record_decision(tool_name, args, "timeout_rejected", risk_level)
                return {'decision': 'reject', 'args': args}
            
            response = response.strip().lower()
            
            if response in ('a', 'approve', 'yes', 'y'):
                self._record_decision(tool_name, args, "approved", risk_level)
                return {'decision': 'approve', 'args': args}
            elif response in ('s', 'session'):
                # Approve for entire session — cache this tool+args signature
                sig = self._args_signature(tool_name, args)
                self.approval_cache[sig] = "approved"
                self._record_decision(tool_name, args, "approved", risk_level)
                print(f"  {Colors.GREEN}✅ Approved for rest of session.{Colors.RESET}")
                return {'decision': 'approve', 'args': args}
            elif response in ('m', 'modify', 'redirect'):
                print(f"  {Colors.CYAN}Enter modified args as JSON (or press Enter to cancel):{Colors.RESET}")
                try:
                    new_args_str = input(f"  {Colors.CYAN}> {Colors.RESET}").strip()
                    if not new_args_str:
                        self._record_decision(tool_name, args, "rejected", risk_level)
                        return {'decision': 'reject', 'args': args}
                    new_args = json.loads(new_args_str)
                    self._record_decision(tool_name, args, "redirected", risk_level)
                    return {'decision': 'redirect', 'args': new_args}
                except json.JSONDecodeError:
                    print(f"  {Colors.RED}[!] Invalid JSON. Rejecting.{Colors.RESET}")
                    self._record_decision(tool_name, args, "rejected", risk_level)
                    return {'decision': 'reject', 'args': args}
            else:
                self._record_decision(tool_name, args, "rejected", risk_level)
                return {'decision': 'reject', 'args': args}
    
    def _check_escalation(self, tool_name: str, args: dict) -> Optional[dict]:
        """Check if arguments escalate the risk level for this tool."""
        rules = self.escalation_rules.get(tool_name)
        if not rules:
            return None
        
        # Always-escalate rule
        if "always_escalate_to" in rules:
            return {
                "escalate_to": rules["always_escalate_to"],
                "reason": f"Tool '{tool_name}' always requires {rules['always_escalate_to']} approval"
            }
        
        # Keyword-based escalation (for execute_command)
        if "keywords" in rules:
            args_str = json.dumps(args, default=str).lower()
            cmd_str = str(args.get("cmd", "")).lower()
            check_str = f"{cmd_str} {args_str}".lower()
            
            for keyword in rules["keywords"]:
                if keyword.lower() in check_str:
                    return {
                        "escalate_to": rules["escalate_to"],
                        "reason": f"Argument contains destructive pattern '{keyword}' — escalated to {rules['escalate_to']}"
                    }
        
        # Argument-based escalation (for fetch_web_content verify=False)
        if "argument_rules" in rules:
            arg_rules = rules["argument_rules"]
            for arg_name, expected_value in arg_rules.items():
                if arg_name == "escalate_to":
                    continue
                if arg_name in args and args[arg_name] == expected_value:
                    escalate_to = arg_rules.get("escalate_to", "high")
                    return {
                        "escalate_to": escalate_to,
                        "reason": f"Argument '{arg_name}={expected_value}' escalates risk to {escalate_to}"
                    }
        
        # Path-based escalation (for cat_local_file)
        if "sensitive_paths" in rules:
            filepath = str(args.get("filename", "")).lower()
            for sensitive_path in rules["sensitive_paths"]:
                if sensitive_path.lower() in filepath:
                    return {
                        "escalate_to": rules["escalate_to"],
                        "reason": f"Accessing sensitive path '{sensitive_path}' — escalated to {rules['escalate_to']}"
                    }
        
        return None
    
    def _args_signature(self, tool_name: str, args: dict) -> str:
        """Generate a hash signature for a tool+args combo for session caching."""
        args_str = json.dumps(args, sort_keys=True, default=str)
        args_hash = hashlib.md5(args_str.encode()).hexdigest()[:12]
        return f"{tool_name}:{args_hash}"
    
    def _get_risk_reason(self, tool_name: str, risk_level: str) -> str:
        """Get a human-readable reason for why a tool has this risk level."""
        reasons = {
            "execute_command": "Executes arbitrary shell commands on the host",
            "install_python_package": "Installs packages from PyPI — can modify system state",
            "fuzz_web_endpoint": "Active security testing against external endpoints",
            "fetch_web_content": "Makes network requests to external servers",
            "cat_local_file": "Reads files from the local filesystem",
            "save_to_local_file": "Writes files to the local filesystem",
        }
        return reasons.get(tool_name, f"Classified as {risk_level} risk")
    
    def _record_decision(self, tool_name: str, args: dict, decision: str, risk_level: str) -> None:
        """Record a HITL decision in the audit log and telemetry."""
        entry = {
            "tool": tool_name,
            "decision": decision,
            "risk_level": risk_level,
            "args_preview": json.dumps(args, default=str)[:200]
        }
        self.session_approvals.append(entry)
        logger.info(f"HITL {decision.upper()}: {tool_name} ({risk_level}) — args: {entry['args_preview'][:100]}")
        
        # Record in telemetry if available
        import core.telemetry
        if core.telemetry.session_telemetry:
            core.telemetry.session_telemetry.record_hitl_decision(tool_name, decision, risk_level)