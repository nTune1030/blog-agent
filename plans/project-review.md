# Comprehensive Project Review — basic-agent

**Date**: 2026-05-16  
**Scope**: Full codebase review after HITL implementation  

---

## Executive Summary

The basic-agent framework is a well-structured, modular system for building autonomous AI agents with a ReAct loop, dynamic plugin system, and HITL authorization. The HITL implementation is solid and follows the user's directive of **no sandboxing** — the human is the safety layer, not code restrictions. The project is functional and well-documented.

However, there are **1 critical bug**, **3 high-priority issues**, and several medium/low items that should be addressed before production use.

---

## 🔴 Critical Bug: HITL Redirect Decision Mismatch

**File**: [`core/hitl.py`](core/hitl.py:326) vs [`core/agent.py`](core/agent.py:485)

When an operator chooses to **modify args** (the `[m]` option), `HITLAuthorizer.request_approval()` returns:

```python
# hitl.py line 326
return {'decision': 'approve', 'args': new_args}
```

But `BaseAgent.chat()` checks for:

```python
# agent.py line 485
elif approval['decision'] == 'redirect':
    args = approval['args']
```

The decision value `'approve'` will never match `'redirect'`, so **the operator's modified args are silently discarded** and the original args are used instead. This is a functional bug — the redirect feature doesn't work.

**Fix**: Change `hitl.py` line 326 to return `{'decision': 'redirect', 'args': new_args}` to match what the agent expects.

---

## 🟠 High-Priority Issues

### 1. `install_python_package` — Command Injection Vector

**File**: [`tools/python_ops.py`](tools/python_ops.py:8)

```python
def install_python_package(package_name: str) -> str:
    cmd = f"pip install {package_name}"
    return execute_command(cmd, timeout=120)
```

The `package_name` parameter is interpolated directly into a shell command with **no sanitization**. A malicious LLM output like `package_name: "requests; rm -rf /"` would execute arbitrary commands. While HITL gates this at the agent level, the tool itself should still validate input.

**Recommendation**: Add basic validation — reject package names containing `;`, `&`, `|`, `&&`, `||`, or any shell metacharacters.

### 2. No `requirements.txt` or Dependency Management

The project has no `requirements.txt`, `pyproject.toml`, or `Pipfile`. The README lists `pip install ollama chromadb duckduckgo-search flask` but there's no pinned versions. This makes reproducibility impossible and will cause breakage as dependencies update.

**Recommendation**: Create a `requirements.txt` with pinned versions:
```
ollama>=0.4.0
chromadb>=0.4.0
duckduckgo-search>=4.0.0
requests>=2.31.0
beautifulsoup4>=4.12.0
flask>=3.0.0
```

### 3. No `.gitignore`

The project has no `.gitignore`. The `sessions/` directory (containing runtime logs), `memory/chroma.sqlite3` (binary DB), and `__pycache__/` directories would all be committed to version control.

**Recommendation**: Add a `.gitignore`:
```
__pycache__/
*.pyc
sessions/session_*/
memory/chroma.sqlite3
memory/*.sqlite3
.env
```

---

## 🟡 Medium-Priority Issues

### 4. `chat()` Method is a 335-Line Monolith

**File**: [`core/agent.py`](core/agent.py:279)

The `chat()` method handles streaming, tool parsing, HITL evaluation, parallel execution, loop detection, context pruning, error recovery, and think-stall detection all in one method. This makes it difficult to test, debug, or extend.

**Recommendation**: Decompose into methods:
- `_stream_response()` — handle LLM streaming with retry
- `_process_tool_calls()` — validate, HITL-evaluate, and execute tools
- `_handle_empty_response()` — deal with empty LLM outputs
- `_inject_kb_context()` — knowledge base context injection

### 5. Module-Level Side Effects in `rag_ops.py`

**File**: [`tools/rag_ops.py`](tools/rag_ops.py:14)

```python
rag_chroma_client = chromadb.PersistentClient(path=MEMORY_DIR)
rag_collection = rag_chroma_client.get_or_create_collection(name="knowledge_cache")
ollama_client = Client()
```

These execute at import time. If ChromaDB or Ollama isn't running/installed, the entire module fails to load and `main.py` catches the `ModuleNotFoundError` but not the `ConnectionError` or other runtime errors from these initializations.

**Recommendation**: Use lazy initialization — create these on first use rather than at module level.

### 6. `debug_mode` is a No-Op

**File**: [`core/agent.py`](core/agent.py:68) and [`main.py`](main.py:109)

```python
# agent.py line 68
self.debug_mode = False

# main.py line 108-110
if user_input.lower() == 'debug':
    agent.debug_mode = not agent.debug_mode
```

The `debug_mode` flag is toggled but never read anywhere in the agent logic. It appears to be a leftover from the pre-HITL approval system.

**Recommendation**: Either remove it or implement actual debug behavior (e.g., verbose logging, skip HITL, print tool args before execution).

### 7. Config Default Missing HITL Section

**File**: [`core/config.py`](core/config.py:14)

`DEFAULT_CONFIG` doesn't include the `hitl` section. If `config.json` is missing and defaults are used, `HITLAuthorizer.__init__` will get an empty dict for `self.config`, and `self.enabled` will default to `True` (which is correct), but `escalation_keywords` and `sensitive_paths` won't be loaded from config.

**Recommendation**: Add HITL defaults to `DEFAULT_CONFIG`:
```python
"hitl": {
    "enabled": True,
    "medium_requires_approval": False,
    "auto_approve_session": True,
    "escalation_keywords": {"critical": [...]},
    "sensitive_paths": [...],
    "risk_overrides": {}
}
```

### 8. `syntax_model` References Non-Existent Config Key

**File**: [`tools/rag_ops.py`](tools/rag_ops.py:19)

```python
syntax_model = APP_CONFIG.get("agent", {}).get("default_model", "base-model:latest")
```

The config uses `local_model` and `cloud_model`, not `default_model`. This will always fall back to `"base-model:latest"` which likely doesn't exist.

**Recommendation**: Change to `APP_CONFIG.get("agent", {}).get("local_model", "qwen-coder:latest")`.

### 9. Duplicate Comment in `agent.py`

**File**: [`core/agent.py`](core/agent.py:441)

```python
# Process tool calls
# Process tool calls
if tool_calls:
```

Minor but should be cleaned up.

---

## 🟢 Low-Priority / Style Issues

### 10. PEP 8 — Multiple Imports on One Line

**File**: [`core/agent.py`](core/agent.py:11)

```python
import os, subprocess, json, logging, time
```

PEP 8 recommends one import per line.

### 11. `os.system('')` Hack for ANSI Colors

**File**: [`core/config.py`](core/config.py:76)

```python
os.system('')  # Enable ANSI colors
```

This works on Windows but is a side effect at module level. Consider using `colorama.init()` or documenting this clearly.

### 12. `save_note` Uses LOGS_DIR Instead of MEMORY_DIR

**File**: [`tools/memory_ops.py`](tools/memory_ops.py:14)

```python
filepath = os.path.join(LOGS_DIR, filename)
```

The docstring says "Save textual note permanently" but it saves to the session-specific `LOGS_DIR`, which is ephemeral. For permanent notes, `MEMORY_DIR` would be more appropriate.

### 13. Magic Numbers in Context Pruning

**File**: [`core/agent.py`](core/agent.py:125)

```python
keep_recent = 15 if self.mode == "cloud" else 8
```

These should be configurable via `config.json` or at least named constants.

### 14. `GLOBAL_WEB_SESSION` Thread Safety

**File**: [`tools/web_ops.py`](tools/web_ops.py:18)

```python
GLOBAL_WEB_SESSION = requests.Session()
```

A module-level `requests.Session()` is used for stateful web interactions. While `requests.Session` is thread-safe for basic operations, cookie mutations across parallel tool calls could cause unexpected behavior.

### 15. No Graceful Shutdown for Telemetry

If the process is killed (not KeyboardInterrupt), the telemetry manifest is lost. Consider registering an `atexit` handler or writing the manifest incrementally.

---

## ✅ What's Working Well

1. **HITL Architecture** — Clean separation of concerns. The `HITLAuthorizer` class is well-structured with escalation rules, session caching, and audit logging. The `@hitl_risk` decorator pattern is elegant.

2. **Dynamic Plugin System** — Reflection-based tool discovery with graceful dependency error handling is solid.

3. **Parallel Tool Execution** — `ThreadPoolExecutor` with `as_completed` is the right pattern for concurrent tool calls.

4. **Token-Aware Context Pruning** — The `_compress_history()` method with recap generation is a smart approach to context window management.

5. **Documentation** — README, creating_plugins.md, and inline docstrings are comprehensive and well-maintained.

6. **Telemetry** — Session manifest with HITL decision tracking provides good observability.

7. **Argument-Level Escalation** — The pattern of escalating `verify=False` to HIGH and destructive command keywords to CRITICAL is well-designed.

8. **No Sandboxing** — Per the user's explicit requirement, the agent retains full capability. HITL is a human approval gate, not a restriction system.

---

## Cloud Readiness Assessment

| Area | Status | Notes |
|------|--------|-------|
| Async I/O | ❌ | Entirely synchronous — will block on LLM calls |
| API Authentication | ⚠️ | Only OLLAMA_API_KEY env var |
| Rate Limiting | ❌ | No rate limits on tool execution |
| Session Persistence | ❌ | No session resumption capability |
| Containerization | ❌ | No Dockerfile or docker-compose |
| External Logging | ❌ | Only local file logging |
| Multi-Process Safety | ⚠️ | ChromaDB SQLite won't scale; GLOBAL_WEB_SESSION not shared |
| Health Checks | ❌ | No readiness/liveness endpoints |
| Dependency Management | ❌ | No requirements.txt with pinned versions |

---

## Recommended Priority Order

1. **🔴 Fix HITL redirect bug** — `hitl.py` line 326 should return `'redirect'` not `'approve'`
2. **🟠 Add `requirements.txt`** with pinned dependencies
3. **🟠 Add `.gitignore`** 
4. **🟠 Sanitize `install_python_package` input**
5. **🟡 Add HITL defaults to `DEFAULT_CONFIG`**
6. **🟡 Fix `syntax_model` config key**
7. **🟡 Remove duplicate comment in `agent.py`**
8. **🟡 Decide on `debug_mode` — implement or remove**
9. **🟢 Refactor `chat()` into smaller methods**
10. **🟢 Lazy-initialize `rag_ops.py` module-level connections**