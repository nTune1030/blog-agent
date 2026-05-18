# 🚀 START HERE — Specialize Your Agent in 5 Minutes

> **You have a framework. Now make it yours.**
> This guide walks you through turning the base template into a specialized agent — no framework code changes needed.

Built by [NTUNE1030](https://github.com/NTUNE1030) · [View on GitHub](https://github.com/NTUNE1030/blog-agent)

---

## ⚡ The 30-Second Version

```
1. Edit  →  agent_profile.json    (who is your agent?)
2. Edit  →  config.json            (what needs approval?)
3. Drop  →  tools/*.py             (what can it do?)
4. Run   →  python main.py        (go!)
```

That's it. Everything else is optional customization.

---

## 🧑‍💻 Step 1 — Define Your Agent's Identity

Edit **`agent_profile.json`** at the project root. This is the single source of truth for your agent's personality, model preferences, and behavioral rules.

```json
{
  "name": "DevOps Assistant",
  "local_model": "qwen-coder:latest",
  "cloud_model": "qwen3-coder:480b-cloud",
  "system_prompt": "You are a DevOps automation assistant. You help with CI/CD pipelines, Docker, Kubernetes, and infrastructure management. Always verify commands before executing them.",
  "rules": {
    "docker_safety": {
      "keywords": ["docker rm", "docker rmi", "docker system prune"],
      "message": "WARNING: Docker destructive command detected. Always confirm with the operator before proceeding."
    },
    "production_warning": {
      "keywords": ["production", "prod", "live"],
      "message": "CAUTION: You are operating in a production context. Double-check all commands before execution."
    }
  }
}
```

### What each field does

| Field | Purpose |
|-------|---------|
| `name` | Displayed on startup. Helps you identify which agent you're running. |
| `local_model` | Ollama model used in local mode (GPU). |
| `cloud_model` | Ollama model used in cloud mode (API). |
| `system_prompt` | The core instruction set that defines your agent's behavior and expertise. |
| `rules` | Dynamic rules injected when specific keywords appear in the conversation. |

💡 **Pro tip**: Write a detailed `system_prompt` — it's the #1 factor in agent quality. Include what the agent should do, what it should avoid, and how it should format responses.

---

## 🛡️ Step 2 — Configure Safety & Approvals

Edit **`config.json`** to control which tool calls require your approval before execution.

```json
{
  "hitl": {
    "enabled": true,
    "medium_requires_approval": false,
    "auto_approve_session": true,
    "approval_timeout_seconds": 300,
    "escalation_keywords": {
      "critical": ["rm ", "del ", "format", "mkfs", "shutdown", "reboot"]
    },
    "sensitive_paths": ["/etc/passwd", "/etc/shadow", "~/.ssh"],
    "risk_overrides": {
      "execute_command": "high",
      "fuzz_web_endpoint": "critical"
    }
  }
}
```

### Risk levels at a glance

| Level | Behavior | When to use |
|-------|----------|-------------|
| 🟢 **LOW** | Auto-approve | Read-only, search, safe queries |
| 🟡 **MEDIUM** | Auto-approve (configurable) | File writes, web fetches |
| 🟠 **HIGH** | Require operator approval | Shell commands, package installs |
| 🔴 **CRITICAL** | Require explicit "YES" | Destructive ops, fuzzing, dangerous patterns |

### Quick configs

**Maximum safety** — approve everything:
```json
"medium_requires_approval": true
```

**Hands-off** — auto-approve everything:
```json
"enabled": false
```

**No timeout** — wait forever for approval:
```json
"approval_timeout_seconds": 0
```

---

## 🔧 Step 3 — Add Your Custom Tools

Drop Python files into the **`tools/`** directory. Each function becomes a tool the agent can call. The framework auto-discovers them.

### Minimal tool template

```python
# tools/my_tool.py

def greet_user(name: str) -> str:
    """Greet a user by name. Use this when meeting someone new."""
    return f"Hello, {name}! I'm your DevOps assistant."
```

That's it. The framework will:
1. ✅ Discover the function automatically
2. ✅ Register it as a skill the LLM can call
3. ✅ Use the docstring as the tool's instruction manual
4. ✅ Classify its risk level (LOW by default)

### Tool with HITL risk declaration

```python
# tools/deploy_ops.py
from core.hitl import hitl_risk, RiskLevel

@hitl_risk(RiskLevel.HIGH)
def deploy_to_server(app_name: str, environment: str, version: str) -> str:
    """Deploy an application to a server. Requires operator approval."""
    # Your deployment logic here
    return f"Deployed {app_name} v{version} to {environment}"
```

### Async tool for concurrent I/O

```python
# tools/api_ops.py
import aiohttp

async def async_check_endpoints(urls: list) -> str:
    """Check multiple API endpoints concurrently for health status."""
    results = []
    async with aiohttp.ClientSession() as session:
        for url in urls:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                results.append(f"{url}: {resp.status}")
    return "\n".join(results)
```

### The 3 rules of good tools

| Rule | Why |
|------|-----|
| **Type-hint all parameters** | The LLM needs to know what types to pass |
| **Return `str`** | The LLM reads tool output as text in its context |
| **Write clear docstrings** | This becomes the LLM's instruction manual for when and how to use the tool |

---

## 🏃 Step 4 — Run Your Agent

```powershell
# Install dependencies
pip install -r requirements.txt

# Interactive mode (prompts for compute environment)
python main.py

# Skip the prompt — go straight to local mode
python main.py --mode local

# Use a specific model
python main.py --mode local --model llama3:latest

# Resume a crashed/interrupted session
python main.py --resume sessions/session_20260516_113012

# See which sessions have saved state
python main.py --list-sessions
```

---

## 🗺️ Project Map

```
Where to change things:
─────────────────────────────────────────────────
🎨 Agent personality    →  agent_profile.json
🛡️ Safety & approvals  →  config.json
🔧 What it can do      →  tools/*.py
🪝 Pre/post hooks      →  core/hooks.py
📊 Telemetry tracking   →  core/telemetry.py
🛑 Risk classification  →  core/hitl.py
🧠 Core agent loop      →  core/agent.py
⚙️ Config & logging     →  core/config.py
```

```
What NOT to touch (unless you know what you're doing):
─────────────────────────────────────────────────
core/agent.py     — The ReAct loop, tool dispatch, context pruning
core/config.py    — Path resolution, structured logging setup
main.py           — Bootloader, plugin discovery, session lifecycle
```

---

## 🎯 Common Specializations

### DevOps Agent
```json
// agent_profile.json
{
  "name": "DevOps Bot",
  "system_prompt": "You are a DevOps automation assistant. Help with CI/CD, Docker, K8s, and infrastructure. Always verify destructive commands.",
  "rules": {
    "production_guard": {
      "keywords": ["production", "prod"],
      "message": "CAUTION: Production environment detected. Verify all commands."
    }
  }
}
```
Tools to add: `deploy_ops.py`, `k8s_ops.py`, `ci_ops.py`

### Security Researcher Agent
```json
// agent_profile.json
{
  "name": "Security Researcher",
  "system_prompt": "You are a security research assistant. Help with vulnerability assessment, recon, and reporting. Never exploit without explicit approval.",
  "rules": {}
}
```
Tools to add: Already has `fuzz_web_endpoint` (CRITICAL risk), `fetch_web_content`

### Data Analyst Agent
```json
// agent_profile.json
{
  "name": "Data Analyst",
  "system_prompt": "You are a data analysis assistant. Help with data cleaning, visualization, statistical analysis, and reporting.",
  "rules": {}
}
```
Tools to add: `csv_ops.py`, `chart_ops.py`, `stats_ops.py`

---

## 🔑 Key Concepts

### HITL Approval Flow

```
Agent calls tool → HITL evaluates risk → 
  ├─ LOW/MEDIUM → Auto-approve → Execute
  ├─ HIGH → Show approval prompt → 
  │    ├─ [a] Approve → Execute
  │    ├─ [r] Reject → Tell agent to try different approach
  │    ├─ [m] Modify args → Execute with new args
  │    ├─ [s] Approve for session → Execute (cache approval)
  │    └─ ⏱ Timeout → Auto-reject
  └─ CRITICAL → Show YES confirmation → 
       ├─ Type "YES" → Execute
       └─ Anything else / Timeout → Reject
```

### Session Resumption

The agent auto-saves its state after every exchange. If interrupted:
1. State is saved to `sessions/session_*/agent_state.json`
2. Run `python main.py --list-sessions` to see available sessions
3. Run `python main.py --resume <path>` to pick up where you left off

### Structured Logging

All logs are written as JSON objects to `logs/agent.log`:
```json
{"timestamp": "2026-05-16T16:07:38+00:00", "level": "INFO", "logger": "BaseAgent", "message": "TOOL CALL: execute_command", "module": "agent", "function": "_process_tool_calls", "line": 465, "session_id": "20260516_113012"}
```

---

## 🐛 Troubleshooting

| Problem | Solution |
|---------|----------|
| `ModuleNotFoundError` on startup | Run `pip install -r requirements.txt` |
| Agent not finding your tool | Make sure the function isn't prefixed with `_` and has a docstring |
| HITL not prompting for approval | Check `config.json` → `"hitl" → "enabled": true` |
| Model not found | Run `ollama pull <model_name>` or check `agent_profile.json` |
| Approval hanging forever | Set `"approval_timeout_seconds": 300` in config |
| Session crashed | Run `python main.py --list-sessions` then `--resume <path>` |
| Plugin skipped on load | Install the missing dependency shown in the error message |

---

## 📚 Further Reading

| Document | What it covers |
|----------|---------------|
| [`README.md`](README.md) | Full architecture, all features, configuration reference |
| [`docs/creating_plugins.md`](docs/creating_plugins.md) | Plugin anatomy, HITL risk levels, async tools, hooks |
| [`tools/plugin_template.py`](tools/plugin_template.py) | Copy-paste template for new tools |

---

*Now go build something awesome. 🤖*