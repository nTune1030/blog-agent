"""
Main bootloader for the Blog Agent framework.
Initializes the agent, loads plugins, registers HITL risk levels, and runs the ReAct loop.

HITL (Human-In-The-Loop) authorization is enabled by default. Each plugin function
is automatically classified with a risk level (LOW/MEDIUM/HIGH/CRITICAL) during
loading. HIGH and CRITICAL tools require operator approval before execution.
Use @hitl_risk(RiskLevel.HIGH) on your tool functions to declare explicit risk levels.

Session Resumption: Use --resume <session_dir> to restore a previous session's
chat history and agent state. Use --list-sessions to see available sessions.

Async I/O: Tools decorated with `async def` are automatically detected and run
via asyncio.run(). The aiohttp session is closed cleanly on shutdown.
"""
import os
import asyncio
import argparse
import importlib.util
import inspect
import sys

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')  # type: ignore[attr-defined]

from core.agent import BaseAgent
from core.config import Colors, CURRENT_SESSION_DIR, SESSIONS_BASE
import core.telemetry
from core.telemetry import init_telemetry
from core.hitl import RiskLevel, classify_default_risk


def list_sessions() -> list[str]:
    """List available session directories that contain agent_state.json."""
    sessions = []
    if not os.path.exists(SESSIONS_BASE):
        return sessions
    for entry in sorted(os.listdir(SESSIONS_BASE), reverse=True):
        session_path = os.path.join(SESSIONS_BASE, entry)
        if os.path.isdir(session_path) and os.path.exists(os.path.join(session_path, "agent_state.json")):
            sessions.append(session_path)
    return sessions


def load_plugins(agent, tools_dir=None):
    """Scans the tools directory and auto-registers every function as a skill."""
    if tools_dir is None:
        tools_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools")
    print(f"{Colors.GRAY}[*] Scanning '{tools_dir}' for plugins...{Colors.RESET}")
    
    if not os.path.exists(tools_dir):
        print(f"{Colors.YELLOW}[!] Plugin directory '{tools_dir}' not found. Skipping.{Colors.RESET}")
        return

    # Loop through every file in the tools folder
    for filename in os.listdir(tools_dir):
        if filename.endswith(".py") and not filename.startswith("__"):
            module_name = filename[:-3] # Strip the .py extension
            filepath = os.path.join(tools_dir, filename)

            try:
                # 1. Load the file as a Python module dynamically
                spec = importlib.util.spec_from_file_location(module_name, filepath)
                if spec is None or spec.loader is None:
                    print(f"{Colors.RED}[!] Failed to create module spec for '{filename}'{Colors.RESET}")
                    continue
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)

                # 2. Inspect the module for available functions
                for name, func in inspect.getmembers(module, inspect.isfunction):
                    # Safety check: Only register functions actually written in this file
                    if func.__module__ == module_name and not name.startswith("_"):
                        
                        # Grab the docstring to use as the LLM's tool description
                        description = func.__doc__ or f"Executes the {name} function."
                        
                        # Wire it into the agent
                        agent.register_skill(func, description)
                        
                        # Register HITL risk level for this skill
                        risk_level = getattr(func, '_hitl_risk', None) or classify_default_risk(func)
                        agent.hitl.register_risk(name, risk_level)
            except ModuleNotFoundError as e:
                print(f"{Colors.YELLOW}[!] Skipped plugin '{filename}' - Missing dependency: {e.name}. Fix: pip install {e.name}{Colors.RESET}")
            except Exception as e:
                print(f"{Colors.RED}[!] Failed to load plugin '{filename}': {e}{Colors.RESET}")


def parse_args():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Autonomous Framework - Base Agent")
    parser.add_argument("--mode", choices=["local", "cloud"], default=None,
                        help="Compute mode: 'local' (default) or 'cloud'")
    parser.add_argument("--model", default=None,
                        help="Override the model name (e.g. 'qwen-coder:latest')")
    parser.add_argument("--resume", default=None, metavar="SESSION_DIR",
                        help="Resume a previous session from its directory path")
    parser.add_argument("--list-sessions", action="store_true",
                        help="List available sessions with saved state and exit")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()

    # List sessions and exit
    if args.list_sessions:
        sessions = list_sessions()
        if not sessions:
            print(f"{Colors.YELLOW}[!] No resumable sessions found in {SESSIONS_BASE}{Colors.RESET}")
        else:
            print(f"{Colors.CYAN}Available sessions with saved state:{Colors.RESET}")
            for s in sessions:
                print(f"  {s}")
        sys.exit(0)

    print("=========================================")
    print("      BASE AGENT FRAMEWORK ACTIVE        ")
    print("=========================================")
    print(f"{Colors.CYAN}🚀 Autonomous Framework v1{Colors.RESET}")

    # Determine compute mode
    if args.mode:
        compute_mode = args.mode
    else:
        print("\nSelect Compute Environment:")
        print("1. Local Machine (Uses RTX GPU)")
        print("2. Cloud Hosted (Uses Ollama API)")
        
        try:
            choice = input("\n[Operator] Choice (1 or 2) > ")
        except (KeyboardInterrupt, EOFError):
            print(f"\n{Colors.RED}[!] Exiting...{Colors.RESET}")
            sys.exit(1)
            
        compute_mode = "cloud" if choice.strip() == "2" else "local"
    
    init_telemetry(CURRENT_SESSION_DIR)
    agent = BaseAgent(mode=compute_mode, model_name=args.model)
    
    # Load plugins
    load_plugins(agent)
    
    # Print HITL status
    hitl_enabled = agent.hitl.enabled
    hitl_status = f"{Colors.GREEN}ENABLED{Colors.RESET}" if hitl_enabled else f"{Colors.YELLOW}DISABLED{Colors.RESET}"
    print(f"{Colors.CYAN}🛡️  HITL Authorization: {hitl_status}{Colors.RESET}")
    if hitl_enabled:
        registered = len(agent.hitl.risk_registry)
        high_risk = sum(1 for r in agent.hitl.risk_registry.values() if r in (RiskLevel.HIGH, RiskLevel.CRITICAL))
        print(f"{Colors.GRAY}    {registered} tools registered, {high_risk} require operator approval{Colors.RESET}")
    
    # Resume from previous session if requested
    if args.resume:
        resumed = agent.load_state(args.resume)
        if not resumed:
            print(f"{Colors.YELLOW}[!] Continuing with fresh session.{Colors.RESET}")
    
    print(f"\n{Colors.GREEN}✅ All plugins loaded dynamically. Type 'quit' to exit.{Colors.RESET}")
    
    try:
        while True:
            try:
                user_input = input(f"\n{Colors.CYAN}>>>{Colors.RESET} ").strip()
                if user_input.lower() in ('quit', 'exit'):
                    print(f"{Colors.YELLOW}[!] Exiting...{Colors.RESET}")
                    break
                if not user_input:
                    continue
                    
                response = agent.chat(user_input)
                print(f"\n{Colors.GREEN}🤖 Response:{Colors.RESET} {response}")
                
                # Save state after each chat exchange for crash recovery
                agent.save_state()
                
            except KeyboardInterrupt:
                print(f"\n{Colors.RED}[!] Exiting...{Colors.RESET}")
                break
            except Exception as e:
                print(f"{Colors.RED}[!] Error: {e}{Colors.RESET}")
    finally:
        # Final state save, async cleanup, and telemetry flush
        agent.save_state()
        
        # Close async aiohttp session if it was created
        try:
            from tools.web_ops import close_async_session
            asyncio.run(close_async_session())
        except Exception:
            pass  # Session may not have been created
        
        print(f"{Colors.GRAY}[*] Saving session telemetry...{Colors.RESET}")
        if core.telemetry.session_telemetry:
            core.telemetry.session_telemetry.save_manifest()
