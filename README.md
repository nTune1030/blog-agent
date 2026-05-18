# 🤖 Blog Agent — Autonomous Promotion Framework

A modular framework for building fully autonomous, self-correcting AI agents powered by large language models. Built by **NTUNE1030**, this project provides a robust foundation—including a ReAct-style core loop, **Human-In-The-Loop (HITL) authorization**, tool dynamic registration, persistent Vector DB memory, and execution telemetry—allowing you to easily construct and deploy specialized personas (e.g., Data Analysts, DevOps Assistants, Web Researchers, **Promotion Agents**) simply by editing a configuration file and dropping in new python plugins.

---

## Architecture

```text
┌──────────────────────────────────────────────────────────────────┐
│                     WINDOWS HOST (Operator)                      │
│                                                                  │
│  ┌─────────────────┐    ┌──────────────────────────────────────┐ │
│  │ Local GPU /     │◄── │  main.py (Agent Bootloader)          │ │
│  │ Cloud API       │    │  ├─ core/agent.py (ReAct Loop)       │ │
│  └─────────────────┘    │  ├─ core/hitl.py (HITL Auth Gate)    │ │
│                         │  ├─ core/telemetry.py (Tracking)     │ │
│                         │  ├─ core/hooks.py (Interceptors)      │ │
│                         │  └─ tools/ (Dynamic Plugins)          │ │
│                         └──────────┬───────────────────────────┘ │
│                                    │                             │
│  ┌─────────────────┐               │    ┌─────────────────────┐ │
│  │ agent_profile.  │               │    │  HITL Approval Gate  │ │
│  │ json (Identity) │               ├───►│  LOW → Auto-approve  │ │
│  └─────────────────┘               │    │  MED → Auto-approve  │ │
│                                     │    │  HIGH → Ask operator │ │
│  ┌───────────────┐                  │    │  CRIT → YES confirm  │ │
│  │ config.json   │                  │    │  ⏱ Timeout → Reject  │ │
│  │ (HITL Config) │                  │    └─────────────────────┘ │
│  └───────────────┘                  │                             │
│                         ┌──────────▼───────────────────────────┐ │
│  ┌───────────────┐      │  TARGET ENVIRONMENT                  │ │
│  │ sessions/     │      │  (Web, API, Database, local OS,     │ │
│  │ ├ logs/       │◄──── │   or external systems)               │ │
│  │ ├ reports/    │      └──────────────────────────────────────┘ │
│  │ ├ agent_state │                                             │
│  │ └ manifest.json                                             │
│  └───────────────┘                                               │
└─────────────────────────────���────────────────────────────────────┘
```

---

## Features

### 🛡️ Human-In-The-Loop (HITL) Authorization
Every tool call passes through a risk-classification gate before execution. The agent retains **full capability** — HITL does not sandbox or block. It ensures a human approves before dangerous operations execute:

| Risk Level | Behavior | Examples |
|------------|----------|----------|
| **LOW** | Auto-approve | `smart_search_learnings`, `cat_local_file` |
| **MEDIUM** | Auto-approve (configurable) | `save_to_local_file`, `record_learning`, `fetch_web_content` |
| **HIGH** | Require operator approval | `execute_command`, `install_python_package` |
| **CRITICAL** | Require explicit "YES" confirmation | `fuzz_web_endpoint`, commands with `rm -rf`/`del` patterns |

Argument-level escalation automatically promotes risk: `verify=False` on web requests escalates to HIGH, destructive command keywords escalate to CRITICAL. Configure everything in `config.json` under the `hitl` section.

**Approval Timeout**: If the operator doesn't respond within `approval_timeout_seconds` (default: 300s / 5 min), the request is automatically rejected. This prevents the agent from hanging indefinitely on an unattended approval prompt.

### ⏱ HITL Approval Timeout
Configure the timeout in `config.json`:
```json
{
  "hitl": {
    "approval_timeout_seconds": 300
  }
}
```
When the timeout expires, the approval request is auto-rejected and logged as `timeout_rejected` in telemetry. Set to `0` to disable timeout (approval prompts wait indefinitely).

### 🔄 Session Resumption
The agent automatically saves its state (chat history, tried actions, loop detection) to `agent_state.json` after each chat exchange and on shutdown. If the session is interrupted or crashes, you can resume from where you left off:

```powershell
# List available sessions with saved state
python main.py --list-sessions

# Resume a specific session
python main.py --resume sessions/session_20260516_113012

# Start with a specific model and mode
python main.py --mode local --model qwen-coder:latest
```

### 📋 Structured JSON Logging
All log entries are written to `logs/agent.log` as structured JSON objects, making them machine-parseable for monitoring, alerting, and analysis:

```json
{
  "timestamp": "2026-05-16T16:07:38.123456+00:00",
  "level": "INFO",
  "logger": "BaseAgent",
  "message": "TOOL CALL: execute_command | Args: {\"cmd\": \"dir /b\"}",
  "module": "agent",
  "function": "_process_tool_calls",
  "line": 465,
  "session_id": "20260516_113012"
}
```

Pass extra context using `logger.info("message", extra={"extra_fields": {"tool": "web_ops", "url": "..."}})`.

### 🧠 Dual Compute Architectures
At launch, choose between executing models via **Local Compute** (using Ollama and your local GPU) or **Cloud Hosted** (using a cloud API endpoint). 

### ⚡ Parallel Tool Execution
When the agent needs to perform multiple independent tasks (e.g., executing multiple web searches simultaneously), the ReAct loop automatically dispatches them concurrently via a `ThreadPoolExecutor`, dramatically speeding up execution time per iteration.

### 🚀 Async I/O Support
The framework supports both synchronous and asynchronous tool functions. Tools defined with `async def` are automatically detected and executed via `asyncio.run()`. The `web_ops.py` module includes async versions of fetch and fuzz operations:

- `async_fetch_web_content()` — Async HTTP requests via aiohttp
- `async_fuzz_web_endpoint()` — Concurrent fuzzing with `asyncio.gather()`

These async tools use a shared `aiohttp.ClientSession` for connection pooling, which is automatically closed on shutdown.

### 🔌 Dynamic Plugin System & Dependency Management
No hardcoded tool mapping! Simply drop any Python file containing functions into the `tools/` directory. The bootloader uses reflection to automatically register them as LLM-accessible tools. If a plugin requires an uninstalled third-party package, the framework gracefully skips the plugin and prompts you with the exact `pip install` command needed.

### ✂️ Token-Aware Context Pruning
To prevent LLM crashes (`TokenLimitExceeded`) and reduce API costs, the memory manager automatically tracks approximate token consumption. When the history approaches 80% of the maximum limit, it dynamically summarizes older interactions while keeping the most recent context pristine.

### 🛠️ Pre/Post Execution Interceptors
Use the provided `core/hooks.py` template to build custom intelligence. Intercept arguments before they execute, or analyze tool output afterward to implement custom semantic loop-detection or auto-correction logic specifically for your agent's domain.

### 📚 Persistent Knowledge Base
Includes out-of-the-box generic tools (`memory_ops.py`) that allow the agent to record learned insights into persistent ChromaDB Vector DB nodes, automatically consulting them across reboots.

### 📈 Session Telemetry & Tracking
At the conclusion of a session, a full `session_manifest.json` dashboard is generated tracking tool efficacy, execution errors, HITL approval/rejection rates (including timeout rejections), and LLM Token consumption patterns per specific component phase.

---

## Project Structure

```text
blog-agent/
├── main.py                   # Main bootloader, plugin registry & HITL registration
├── promote.py                # ⭐ Promotion Agent standalone CLI (preview + post)
├── config.json               # Deployment configs + HITL risk settings
├── agent_profile.json        # The single source of truth for the agent's identity
├── promotion_profile.json    # ⭐ Git profile, project metadata & API credentials
├── requirements.txt           # Python dependencies
├── core/
│   ├── agent.py              # Main autonomous ReAct loops + session resumption
│   ├── config.py             # Config parser, path constants, structured JSON logging
│   ├── hitl.py               # HITL authorizer, risk levels, escalation, timeout
│   ├── hooks.py              # Template for tool interception (pre/post execution)
│   ├── promotion.py          # ⭐ Platform template rendering engine
│   └── telemetry.py          # Session measurement singleton + HITL tracking
├── tools/                    # Drop your custom plugins here
│   ├── local_ops.py          # Core local system operations
│   ├── memory_ops.py         # Vector database interactions
│   ├── rag_ops.py            # External searching capabilities
│   ├── web_ops.py            # Web tools (sync + async with aiohttp)
│   ├── python_ops.py         # Python package operations
│   ├── reporting_ops.py      # Report generation tools
│   ├── promotion_ops.py      # ⭐ Agent-callable promotion content tools
│   ├── posting_ops.py        # ⭐ Agent-callable posting tools (CRITICAL risk)
│   └── plugin_template.py    # Template for creating new plugins
├── sessions/                 # Workspace logs
│   └── session_[TIMESTAMP]/
│       ├── logs/             # Structured JSON logs (agent.log)
│       ├── reports/          # Generated reports + promotion markdown
│       ├── agent_state.json  # Session resumption state (auto-saved)
│       └── session_manifest.json # End of session metrics + HITL decisions
└── memory/                   # Chroma DB SQLite structures
```

---

## Installation & Usage

### 1. Install Dependencies
```powershell
pip install -r requirements.txt
```

This installs: `ollama`, `chromadb`, `duckduckgo-search`, `requests`, `beautifulsoup4`, `flask`, `aiohttp`, `praw` (Reddit API), and `tweepy` (Twitter API).

### 2. Configure Your Agent
Edit the root `agent_profile.json` to define what this agent should do:
```json
{
  "name": "My Custom Agent",
  "local_model": "qwen-coder:latest",
  "cloud_model": "qwen3-coder:480b-cloud",
  "system_prompt": "You are a helpful assistant. Use your tools to answer user questions.",
  "rules": {
    "important_rule": {
      "keywords": ["database"],
      "message": "When querying the database, always limit results to 10."
    }
  }
}
```

### 3. Configure HITL Authorization
Edit `config.json` to customize which tools require human approval:
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
      "install_python_package": "high",
      "fuzz_web_endpoint": "critical",
      "fetch_web_content": "medium"
    }
  }
}
```

Set `"enabled": false` to disable HITL and auto-approve all tool calls. Set `"approval_timeout_seconds": 0` to disable the approval timeout.

### 4. Add Custom Tools
Create a python file in the `tools/` directory with a well-documented function:
```python
def fetch_weather(city: str) -> str:
    """Fetches the current weather for a specified city."""
    return "Sunny, 75F"
```

Optionally, declare a risk level using the `@hitl_risk` decorator:
```python
from core.hitl import hitl_risk, RiskLevel

@hitl_risk(RiskLevel.HIGH)
def execute_command(cmd: str, cwd: str = None, timeout: int = 60) -> str:
    """Run local Windows command"""
    ...
```

For async tools (concurrent I/O), use `async def`:
```python
async def async_fetch_data(url: str) -> str:
    """Fetch data asynchronously using aiohttp."""
    import aiohttp
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            return await resp.text()
```

The framework automatically detects async functions and runs them via `asyncio.run()`.

### 5. Start the Framework
```powershell
# Interactive mode (prompts for compute environment)
python main.py

# With command-line options
python main.py --mode local --model qwen-coder:latest
python main.py --resume sessions/session_20260516_113012
python main.py --list-sessions
```

You will be prompted to select **Local** or **Cloud** execution mode (unless `--mode` is specified). The HITL status will be displayed on startup showing how many tools require operator approval.

---

## HITL Approval Flow

When the agent calls a tool that requires approval, you'll see an interactive prompt:

```
============================================================
⚠️  HITL APPROVAL REQUIRED — HIGH RISK
============================================================
  Tool:     execute_command
  Risk:     HIGH
  Reason:   Executes arbitrary shell commands on the host
  Args:     {"cmd": "dir /b"}
  (Auto-reject in 300s if no response)
============================================================
  [a] Approve  [r] Reject  [m] Modify args  [s] Approve for session
  >
```

For **CRITICAL** risk operations, you must type `YES` explicitly:

```
============================================================
🚨 CRITICAL RISK — DESTRUCTIVE OPERATION 🚨
============================================================
  Tool:     fuzz_web_endpoint
  Risk:     CRITICAL
  Reason:   Active security testing against external endpoints
  Args:     {"url": "https://api.example.com/v1/users?id=FUZZ", ...}
  ⚠️  This operation could cause significant damage.
  (Auto-reject in 300s if no response)
============================================================
  Type YES to approve, or anything else to reject:
  >
```

If the timeout expires before you respond, the request is **automatically rejected** and logged.

---

## Session Resumption

If a session is interrupted (crash, Ctrl+C, error), the agent saves its state to `agent_state.json`. Resume with:

```powershell
# See which sessions have saved state
python main.py --list-sessions

# Resume a specific session
python main.py --resume sessions/session_20260516_113012
```

The agent restores chat history, tried actions, loop detection state, and consecutive failure tracking — picking up exactly where it left off.

---

## 📣 Promotion Agent

The Promotion Agent generates **platform-specific promotional content** for your GitHub projects, adapting the message to each platform's cultural norms. A single `promotion_profile.json` drives content for all four supported platforms.

### Supported Platforms

| Platform | Format | Golden Rule |
|----------|--------|-------------|
| **Reddit** (r/netsec, r/HowToHack, r/Python) | Technical post with title + body | No marketing fluff — focus on architecture & problem solved |
| **Hacker News** | "Show HN:" title + required first comment | Strictly factual, no hype words, explain trade-offs |
| **Discord** (Security/CTF servers) | Conversational message with code blocks | Brief, state what's in it for them immediately |
| **X / Twitter** | Tweet with hashtags + mandatory media | Visual hooks, punchy, attach image or GIF |

### Quick Start

```powershell
# Preview all platforms at once
python promote.py

# Preview a specific platform
python promote.py --platform reddit

# Save rendered content to markdown files
python promote.py --save

# Save a specific platform
python promote.py --platform hacker_news --save

# Check which platforms are ready to post (API credentials configured)
python promote.py --readiness

# Dry-run posting (preview what would be posted, no actual posts)
python promote.py --post

# Actually post to all configured platforms (CRITICAL — requires confirmation)
python promote.py --post --confirm

# Post to a specific platform only
python promote.py --post --platform reddit --confirm

# Show platform-specific posting tips
python promote.py --tips --platform discord

# Update your repo URL before rendering
python promote.py --set project.repo_url=https://github.com/NTUNE1030/blog-agent

# Show profile configuration summary
python promote.py --summary
```

### Configure Your Profile

Edit **`promotion_profile.json`** at the project root:

```json
{
  "identity": {
    "name": "Your Name",
    "title": "Your Title & Specialty",
    "bio": "Brief bio for HN first comment context",
    "github_username": "yourusername",
    "github_url": "https://github.com/yourusername",
    "twitter_handle": "@yourhandle",
    "discord_username": "yourname"
  },
  "project": {
    "name": "YourProject",
    "tagline": "One-line description",
    "description": "Full description of what it does",
    "tech_stack": ["Python", "Go", "Bash"],
    "key_features": [
      "Feature 1",
      "Feature 2"
    ],
    "problem_solved": "What specific problem does this solve?",
    "technical_challenge": "What was the hardest technical problem?",
    "target_audience": "Who is this for?",
    "repo_url": "https://github.com/NTUNE1030/blog-agent",
    "good_first_issues": true,
    "status": "active_development"
  },
  "api_credentials": {
    "reddit": {
      "client_id": "your_reddit_app_client_id",
      "client_secret": "your_reddit_app_client_secret",
      "username": "your_reddit_username",
      "password": "your_reddit_password",
      "user_agent": "PromotionAgent/1.0 by yourusername"
    },
    "twitter": {
      "api_key": "your_twitter_api_key",
      "api_secret": "your_twitter_api_secret",
      "access_token": "your_twitter_access_token",
      "access_token_secret": "your_twitter_access_token_secret",
      "bearer_token": "your_twitter_bearer_token"
    },
    "discord": {
      "webhook_urls": ["https://discord.com/api/webhooks/your_webhook_id/your_webhook_token"]
    }
  },
  "platforms": {
    "reddit": { "enabled": true, "subreddits": ["r/netsec"] },
    "hacker_news": { "enabled": true },
    "discord": { "enabled": true, "servers": ["Security/CTF"] },
    "twitter": { "enabled": true }
  }
}
```

### Setting Up API Credentials

**Reddit (PRAW):**
1. Go to https://www.reddit.com/prefs/apps
2. Create a "script" type app
3. Set redirect URI to `http://localhost:8080`
4. Copy the client_id (under the app name) and client_secret
5. Fill in `api_credentials.reddit` in `promotion_profile.json`

**Twitter/X (Tweepy):**
1. Apply for a Developer Account at https://developer.twitter.com/
2. Create a Project and App
3. Generate API Keys and Access Tokens
4. Enable "Read and Write" permissions (not just Read Only)
5. Fill in `api_credentials.twitter` in `promotion_profile.json`

**Discord (Webhooks):**
1. Open your Discord server settings
2. Go to Integrations → Webhooks
3. Create a webhook in the target channel
4. Copy the webhook URL
5. Add it to `api_credentials.discord.webhook_urls` array in `promotion_profile.json`

**Hacker News:** No API exists. You must post manually at https://news.ycombinator.com/submit.

### Using the Agent Interactively

The promotion tools are also available as agent-callable skills when running the full framework:

```powershell
python main.py --mode local
```

Then ask the agent:
- "Render a Reddit post for my project"
- "Preview all platform content"
- "Update my repo URL to https://github.com/NTUNE1030/blog-agent"
- "Show me Discord posting tips"
- "Save all promotion content to files"

### Content Tools (Agent-Callable)

| Tool | Risk | Description |
|------|------|-------------|
| `render_promotion_post(platform)` | LOW | Render content for a specific platform |
| `preview_all_platforms()` | LOW | Preview content for all enabled platforms |
| `save_promotion_content(platform)` | MEDIUM | Save rendered content to markdown files |
| `update_promotion_profile(field, value)` | MEDIUM | Update a profile field (dot-notation) |
| `get_promotion_tips(platform)` | LOW | Get platform-specific posting advice |

### Posting Tools (Agent-Callable — CRITICAL Risk)

All posting tools are classified as **CRITICAL** HITL risk. They require explicit "YES" confirmation before executing. These perform irreversible public actions.

| Tool | Risk | Description |
|------|------|-------------|
| `post_to_reddit(subreddit, title, body)` | CRITICAL | Post a text/link submission to a Reddit subreddit via PRAW |
| `post_to_twitter(tweet_text, media_path)` | CRITICAL | Post a tweet via Tweepy (optional media attachment) |
| `post_to_discord(message, webhook_url)` | CRITICAL | Send a message to a Discord channel via webhook |
| `post_to_all_platforms(dry_run)` | CRITICAL | Post to all configured platforms (dry_run=True for preview) |
| `check_posting_readiness()` | LOW | Check which platforms have API credentials configured |

### Platform-Specific Rules (Auto-Injected)

The agent's `agent_profile.json` includes dynamic rules that are injected when platform keywords are detected in conversation:

- **Reddit rule**: Injected when "reddit", "r/netsec" detected — reminds agent to avoid marketing language
- **HN Rule**: Injected when "hacker news", "show hn" detected — enforces factual title format
- **Discord Rule**: Injected when "discord", "ctf" detected — keeps content brief and conversational
- **Twitter Rule**: Injected when "twitter", "tweet" detected — reminds about mandatory visuals
- **Profile Incomplete Rule**: Injected when placeholder values detected — reminds to update profile

---

## License

This project is provided as a foundational framework template. Modify and extend it as needed for your own independent agents.
