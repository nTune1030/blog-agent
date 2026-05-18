# Creating Custom Plugins & Skills

The Blog Agent framework uses a **Dynamic Plugin System**. This means you never have to hardcode tool mappings into the main agent loop.

To give your agent a new skill, all you need to do is drop a well-formatted Python file into the `tools/` directory. The bootloader (`main.py`) will use reflection to automatically parse your file, register the functions, and expose them to the LLM.

---

## 1. The Anatomy of a Good Tool

For the LLM to understand how to use your tool, you must follow three strict rules:
1. **Type Hinting**: All parameters must be type-hinted (e.g., `query: str`, `count: int`).
2. **Return Types**: Functions should generally return a `str` (or a type that can be easily cast to a string via `str()`), because the LLM needs to read the output in its chat history.
3. **Docstrings**: The function *must* have a clear, descriptive docstring. The bootloader extracts this docstring and passes it directly to the LLM as the "tool instruction manual."

### Example Tool

```python
import os
import requests

def fetch_weather(city: str) -> str:
    """
    Fetches the current weather for a specified city.
    Call this whenever the user asks for meteorological information.
    """
    try:
        # Mock weather API call
        return f"The weather in {city} is currently Sunny and 75°F."
    except Exception as e:
        return f"Error fetching weather: {e}"
```

---

## 2. Managing Dependencies

If your tool requires an external pip package (e.g., `requests`, `playwright`, `boto3`), the framework will handle it gracefully. 

If a user tries to boot the agent without the required package installed, the framework will catch the `ModuleNotFoundError`, skip the plugin, and print an actionable warning:
`[!] Skipped plugin 'my_tool.py' - Missing dependency: requests. Fix: pip install requests`

---

## 3. Parallel Execution

The framework executes tools **concurrently**. If the LLM decides to run `fetch_weather("London")` and `fetch_weather("Tokyo")` at the same time, the framework will spin them up in background threads.

**Rule of Thumb**: Ensure your tools are thread-safe. Avoid mutating global variables within your tool functions.

---

## 4. Async Tools

The framework supports **async tool functions** alongside synchronous ones. If you define a tool with `async def`, the agent automatically detects it and runs it via `asyncio.run()`. This is ideal for I/O-bound operations like HTTP requests:

```python
import aiohttp

async def async_fetch_api(url: str) -> str:
    """Fetch data from an API endpoint asynchronously."""
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            return await resp.text()
```

The `web_ops.py` module already includes async versions of its tools:
- `async_fetch_web_content()` — Async HTTP requests via aiohttp
- `async_fuzz_web_endpoint()` — Concurrent fuzzing with `asyncio.gather()`

Async tools are automatically detected by the agent's `inspect.iscoroutinefunction()` check and executed properly without any additional configuration.

---

## 5. HITL Risk Classification

Every tool call passes through the **Human-In-The-Loop (HITL) authorization gate** before execution. The agent retains full capability — HITL does not sandbox or block. It ensures a human approves before dangerous operations execute.

### Risk Levels

| Risk Level | Behavior | Default Examples |
|------------|----------|------------------|
| **LOW** | Auto-approve | Read-only tools, search tools |
| **MEDIUM** | Auto-approve (configurable) | File writes, web fetches |
| **HIGH** | Require operator approval | Shell commands, package installs |
| **CRITICAL** | Require explicit "YES" confirmation | Fuzzing, destructive patterns |

### Approval Timeout

If the operator doesn't respond within `approval_timeout_seconds` (default: 300s / 5 min), the request is **automatically rejected**. This prevents the agent from hanging indefinitely on an unattended approval prompt. Configure in `config.json`:

```json
{
  "hitl": {
    "approval_timeout_seconds": 300
  }
}
```

Set to `0` to disable the timeout (approval prompts wait indefinitely).

### Declaring Risk with `@hitl_risk`

You can explicitly declare a tool's risk level using the `@hitl_risk` decorator:

```python
from core.hitl import hitl_risk, RiskLevel

@hitl_risk(RiskLevel.HIGH)
def execute_command(cmd: str, cwd: str = None, timeout: int = 60) -> str:
    """Run local system command."""
    ...

@hitl_risk(RiskLevel.LOW)
def search_learnings(query: str) -> str:
    """Search prior learnings from the knowledge base."""
    ...
```

If you don't use the decorator, the framework will automatically classify your tool using a heuristic based on its name and docstring keywords (e.g., functions containing "execute", "run", or "command" default to HIGH).

### Argument-Level Escalation

Even if a tool is classified as MEDIUM, certain argument patterns will automatically escalate the risk:

- **`execute_command`** with destructive keywords (`rm`, `del`, `format`, `shutdown`) → escalates to **CRITICAL**
- **`fetch_web_content`** with `verify=False` → escalates to **HIGH**
- **`cat_local_file`** with sensitive paths (`/etc/passwd`, `~/.ssh`) → escalates to **CRITICAL**

These escalation rules are configured in `config.json` under `hitl.escalation_keywords` and `hitl.sensitive_paths`.

### Overriding Risk Levels

You can override any tool's risk level in `config.json`:

```json
{
  "hitl": {
    "risk_overrides": {
      "my_custom_tool": "high",
      "safe_read_tool": "low"
    }
  }
}
```

---

## 6. Advanced: Using Hooks

If your tool requires specific input formatting, or if you want to catch common LLM hallucination errors *before* the tool executes, use the `core/hooks.py` file:

```python
# In core/hooks.py

def pre_execute(func_name: str, args: dict) -> dict:
    """Hook to modify or annotate tool arguments before execution.
    Called BEFORE HITL evaluation — useful for normalizing args."""
    if func_name == "fetch_weather":
        # Force capitalization
        if "city" in args:
            args["city"] = args["city"].capitalize()
    return args

def post_execute(func_name: str, args: dict, result: str, agent: Any) -> str:
    """Hook to analyze tool output after execution.
    Useful for semantic loop detection or auto-correction logic."""
    return result
```

---

## Quick Start Template

A blank template `plugin_template.py` has been provided in the `tools/` directory. Copy it, rename it, and start writing functions!
