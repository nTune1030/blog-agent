"""
Core agent autonomous ReAct loop and logic.

Includes HITL (Human-In-The-Loop) authorization gate that evaluates every
tool call for risk level before execution. LOW/MEDIUM risk auto-approves;
HIGH/CRITICAL risk requires operator approval. The agent retains full
capability — HITL ensures a human approves before dangerous operations.

Session resumption: chat history and agent state are saved to disk after
each iteration and can be loaded on startup to resume a previous session.
"""

from __future__ import annotations
import os, subprocess, json, logging, time, asyncio, inspect
from ollama import Client
import concurrent.futures
import re
# pyrefly: ignore [missing-import]
import chromadb
from collections import OrderedDict
from datetime import datetime
from typing import Any, Callable
from core.config import Colors, logger, LOGS_DIR, MEMORY_DIR, APP_CONFIG, CURRENT_SESSION_DIR
import core.telemetry
from tools.memory_ops import smart_search_learnings, record_learning
import core.hooks
from core.hitl import HITLAuthorizer, RiskLevel, classify_default_risk

class BaseAgent:
    """Autonomous ReAct agent with parallel tool execution, token-aware memory, and plugin support."""
    
    def __init__(self, mode: str = "local", model_name: str | None = None) -> None:
        self.mode = mode.lower()
        self.skills: dict[str, Callable[..., Any]] = {}
        self.tools: list[Callable[..., Any]] = []
        self.consecutive_failures = 0
        
        # Load identity and model prefs from agent_profile.json
        profile_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "agent_profile.json")
        try:
            with open(profile_path, "r", encoding="utf-8") as f:
                self.agent_profile = json.load(f)
        except Exception as e:
            logger.error(f"Failed to load agent_profile.json: {e}")
            self.agent_profile = {"name": "Blank Agent", "system_prompt": "You are a helpful assistant.", "rules": {}}
            
        print(f"{Colors.GREEN}[*] Loaded Profile: {self.agent_profile.get('name', 'Unknown')}{Colors.RESET}")
        
        # --- THE FORK: Local vs. Cloud ---
        if self.mode == "cloud":
            print(f"{Colors.GRAY}[*] Initializing Cloud Connect...{Colors.RESET}")
            api_key = os.environ.get("OLLAMA_API_KEY")
            if not api_key:
                raise ValueError("[!] OLLAMA_API_KEY environment variable is missing!")
                
            self.client = Client(
                host="https://ollama.com",
                headers={'Authorization': f"Bearer {api_key}"}
            )
            self.model = model_name or self.agent_profile.get("cloud_model", "qwen3-coder:480b-cloud")
            
            # --- Vector DB Initialization for Cloud ---
            self.chroma_client = chromadb.PersistentClient(path=MEMORY_DIR)
            self.collection = self.chroma_client.get_or_create_collection(name="agent_memory")
            
        else:
            print(f"{Colors.GRAY}[*] Initializing Local Compute...{Colors.RESET}")
            self.client = Client() 
            self.model = model_name or self.agent_profile.get("local_model", "qwen-coder:latest")
            
        self.recent_tool_args: OrderedDict[str, None] = OrderedDict()
        self.tried_actions: list[str] = []
        self._injected_rules: set[str] = set()
        
        # Initialize HITL authorizer for human-in-the-loop safety
        self.hitl = HITLAuthorizer(APP_CONFIG)
            
        self.system_prompt = self.agent_profile.get("system_prompt", "You are a helpful assistant.")
        self.chat_history = [{'role': 'system', 'content': self.system_prompt}]
        
        self._ensure_model_exists()

    # ─── Session Resumption ───────────────────────────────────────────

    def save_state(self) -> None:
        """Save current agent state to disk for session resumption.
        
        Persists chat_history, tried_actions, consecutive_failures, and
        recent_tool_args so a crashed or interrupted session can be resumed.
        """
        state = {
            "chat_history": self.chat_history,
            "tried_actions": self.tried_actions,
            "consecutive_failures": self.consecutive_failures,
            "recent_tool_args": list(self.recent_tool_args.keys()),
            "mode": self.mode,
            "model": self.model,
            "saved_at": datetime.now().isoformat(),
        }
        state_path = os.path.join(CURRENT_SESSION_DIR, "agent_state.json")
        try:
            with open(state_path, 'w', encoding='utf-8') as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
            logger.info(f"Agent state saved to {state_path}")
        except Exception as e:
            logger.warning(f"Failed to save agent state: {e}")

    def load_state(self, session_dir: str) -> bool:
        """Load agent state from a previous session directory.
        
        Restores chat_history, tried_actions, consecutive_failures, and
        recent_tool_args so the agent can continue where it left off.
        
        Args:
            session_dir: Path to the previous session directory.
        
        Returns:
            True if state was loaded successfully, False otherwise.
        """
        state_path = os.path.join(session_dir, "agent_state.json")
        if not os.path.exists(state_path):
            print(f"{Colors.YELLOW}[!] No saved state found at {state_path}{Colors.RESET}")
            logger.warning(f"No agent state file at {state_path}")
            return False

        try:
            with open(state_path, 'r', encoding='utf-8') as f:
                state = json.load(f)

            self.chat_history = state.get("chat_history", self.chat_history)
            self.tried_actions = state.get("tried_actions", [])
            self.consecutive_failures = state.get("consecutive_failures", 0)
            recent_keys = state.get("recent_tool_args", [])
            self.recent_tool_args = OrderedDict((k, None) for k in recent_keys)

            saved_at = state.get("saved_at", "unknown")
            msg_count = len(self.chat_history)
            print(f"{Colors.GREEN}[*] Resumed session from {saved_at} ({msg_count} messages loaded){Colors.RESET}")
            logger.info(f"Session resumed from {state_path}: {msg_count} messages, {len(self.tried_actions)} tried actions")
            return True
        except Exception as e:
            print(f"{Colors.RED}[!] Failed to load session state: {e}{Colors.RESET}")
            logger.error(f"Failed to load session state from {state_path}: {e}")
            return False

    def _ensure_model_exists(self) -> None:
        """Verify model existence, compile if missing."""
        if self.mode == "cloud":
            return
            
        print(f"{Colors.GRAY}[*] Checking for model '{self.model}'...{Colors.RESET}")
        try:
            models_info = self.client.list()
            if not any((m.model or '').startswith(self.model) or m.model == f"{self.model}:latest" for m in models_info.models):
                print(f"{Colors.GRAY}[*] Model '{self.model}' not found.{Colors.RESET}")
                modelfile_path = "./Modelfile"
                if os.path.exists(modelfile_path):
                    print(f"{Colors.GRAY}[*] Compiling from {modelfile_path}...{Colors.RESET}")
                    subprocess.run(["ollama", "create", self.model, "-f", modelfile_path], check=True)
                    print(f"{Colors.GREEN}[*] Model compiled.{Colors.RESET}")
                else:
                    print(f"{Colors.RED}[!] Modelfile missing at {modelfile_path}.{Colors.RESET}")
            else:
                print(f"{Colors.GREEN}[*] Model '{self.model}' ready.{Colors.RESET}")
        except Exception as e:
            print(f"{Colors.RED}[!] Warning: {e}{Colors.RESET}")

    def register_skill(self, func: Callable[..., Any], description: str) -> None:
        """Register function as agent skill."""
        name = func.__name__
        self.skills[name] = func
        if description:
            func.__doc__ = description
        self.tools.append(func)
        print(f"{Colors.GRAY}[*] Skill Loaded: {name}{Colors.RESET}")
        logger.info(f"Skill registered: {name}")

    def _compress_history(self) -> None:
        """Compress chat history to preserve context window, based on token limits."""
        context_limit = APP_CONFIG.get("agent", {}).get("context_limits", {}).get(self.mode, 8192)
        
        total_chars = sum(len(str(msg.get('content', ''))) for msg in self.chat_history)
        approx_tokens = total_chars / 4
        
        if approx_tokens < (context_limit * 0.8):
            return
            
        print(f"\n{Colors.YELLOW}[*] Context limit approaching ({int(approx_tokens)}/{context_limit} tokens). Pruning history...{Colors.RESET}")
        
        keep_recent = 15 if self.mode == "cloud" else 8
        if len(self.chat_history) <= keep_recent + 1:
            return
            
        old_messages = self.chat_history[1:-keep_recent]
        recent_messages = self.chat_history[-keep_recent:]
        
        summaries = []
        for msg in old_messages:
            role, c_content, name = msg.get('role', ''), msg.get('content', ''), msg.get('name', '')
            if role == 'tool':
                snippet = str(c_content).replace('\n', ' ')[:120]
                summaries.append(f"  [{name}] -> {snippet}")
            elif role == 'assistant':
                clean = str(c_content).split('\u005d>')[-1].strip() if '\u005d>' in str(c_content) else c_content
                if clean:
                    summaries.append(f"  [ASSISTANT] {str(clean)[:100]}")
            elif role == 'user':
                summaries.append(f"  [OPERATOR] {str(c_content)[:150]}")
        
        if summaries:
            recap_text = "CONTEXT RECAP (Pruned older interactions):\n" + "\n".join(summaries[-30:])
            self.chat_history = [self.chat_history[0], {'role': 'system', 'content': recap_text}, *recent_messages]
            logger.info(f"History compressed: {len(old_messages)} -> 1 recap. Tokens recovered.")

    def _inject_contextual_rules(self, content: str) -> None:
        """Inject dynamic rules based on model output (each rule injected at most once)."""
        content_lower = content.lower()
        new_rules = []
        rules = self.agent_profile.get("rules", {})
        for rule_key, rule_data in rules.items():
            keywords = rule_data.get("keywords", [])
            message = rule_data.get("message", "")
            if rule_key not in self._injected_rules and any(kw in content_lower for kw in keywords):
                self._injected_rules.add(rule_key)
                new_rules.append({'role': 'system', 'content': message})
        self.chat_history.extend(new_rules)

    def _match_args_to_skill(self, args: dict) -> str | None:
        """Match a dict of arguments to a registered skill by parameter names."""
        import inspect
        best_match: str | None = None
        best_score = 0
        for name, func in self.skills.items():
            try:
                sig = inspect.signature(func)
                param_names = set(sig.parameters.keys())
                arg_names = set(args.keys())
                overlap = len(param_names & arg_names)
                if overlap > best_score and overlap >= len(arg_names):
                    best_score = overlap
                    best_match = name
            except (ValueError, TypeError):
                continue
        return best_match

    def parse_almost_json_tool_calls(self, content: str) -> list:
        """Parse tool calls from model output text.
        
        Handles three formats the model may produce:
        1. Tagged JSON with name+arguments keys
        2. Loose JSON with name+arguments keys
        3. Raw args in markdown code blocks (matched to skills by param names)
        """
        tool_calls: list[dict] = []

        # --- PRIMARY: Extract from special tags ---
        tag_pattern = r'\u005cs*\u007b.*?\u007d\s*'
        tag_matches = re.findall(tag_pattern, content, re.DOTALL)
        for raw_json in tag_matches:
            try:
                tool_data = json.loads(raw_json)
                if 'name' in tool_data and 'arguments' in tool_data:
                    tool_calls.append({
                        'function': {
                            'name': tool_data['name'],
                            'arguments': tool_data['arguments']
                        }
                    })
                    logger.info(f"Parsed tag: {tool_data['name']}")
            except json.JSONDecodeError:
                try:
                    fixed = re.sub(r',\s*\}', '}', raw_json)
                    fixed = re.sub(r'(\w+)\s*:', r'"\1":', fixed)
                    tool_data = json.loads(fixed)
                    if 'name' in tool_data and 'arguments' in tool_data:
                        tool_calls.append({
                            'function': {
                                'name': tool_data['name'],
                                'arguments': tool_data['arguments']
                            }
                        })
                except Exception:
                    logger.warning(f"Failed to parse tool_call JSON: {raw_json[:200]}")

        if tool_calls:
            return tool_calls

        # --- SECONDARY: Loose JSON with name + arguments keys ---
        fallback_patterns = [
            r'(\{"type":"function".*?\})',
            r'(\{"name":\s*".*?".*?\})'
        ]
        for fp in fallback_patterns:
            matches = re.findall(fp, content, re.DOTALL)
            for m in matches:
                try:
                    if not m.strip().endswith('}}'):
                        m = m + '}}'
                    tool_data = json.loads(m)
                    if 'function' in tool_data:
                        tool_calls.append({
                            'function': {
                                'name': tool_data['function']['name'],
                                'arguments': tool_data['function'].get('parameters', {})
                            }
                        })
                    elif 'name' in tool_data and 'arguments' in tool_data:
                        tool_calls.append({
                            'function': {
                                'name': tool_data['name'],
                                'arguments': tool_data['arguments']
                            }
                        })
                except (json.JSONDecodeError, Exception):
                    continue

        if tool_calls:
            return tool_calls

        # --- TERTIARY: Raw args in markdown code blocks ---
        code_block_pattern = r'```(?:json)?\s*(\{[^`]+\})\s*```'
        code_matches = re.findall(code_block_pattern, content, re.DOTALL)
        for raw_json in code_matches:
            try:
                args = json.loads(raw_json)
                if isinstance(args, dict) and not ('name' in args and 'arguments' in args):
                    matched_skill = self._match_args_to_skill(args)
                    if matched_skill:
                        tool_calls.append({
                            'function': {
                                'name': matched_skill,
                                'arguments': args
                            }
                        })
                        logger.info(f"Inferred tool from code block args: {matched_skill}")
            except (json.JSONDecodeError, Exception):
                continue

        return tool_calls

    def _inject_kb_context(self, user_input: str) -> None:
        """Auto-inject knowledge base context based on user input keywords."""
        keywords = [w for w in user_input.lower().split() if len(w) > 3]
        for kw in keywords[:3]:
            kb_result = smart_search_learnings(kw)
            if "Found" in kb_result:
                self.chat_history.append({
                    'role': 'system',
                    'content': f"KB CONTEXT:\n{kb_result[:1500]}"
                })
                logger.info(f"KB injected for '{kw}'")
                break

    def _inject_cloud_memory(self) -> None:
        """Inject relevant past strategies from ChromaDB vector memory (cloud mode only)."""
        if self.mode != "cloud":
            return
        try:
            latest_msg = self.chat_history[-1].get('content', '')
            if latest_msg and len(latest_msg) > 10 and self.chat_history[-1].get('role') == 'user':
                results = self.collection.query(
                    query_texts=[latest_msg],
                    n_results=5
                )
                if results and results['documents'] and results['documents'][0]:
                    docs = []
                    for idx, doc in enumerate(results['documents'][0]):
                        if len(docs) < 2:
                            docs.append(doc)
                    if docs:
                        memory_context = "PAST SUCCESSFUL STRATEGIES (from Vector DB):\n" + "\n---\n".join(docs)
                        self.chat_history.append({'role': 'system', 'content': memory_context})
        except Exception as e:
            logger.warning(f"Failed to query ChromaDB: {e}")

    def _stream_response(self, options_dict: dict) -> tuple:
        """Stream LLM response with retry logic. Returns (message_dict, tool_calls_list)."""
        max_retries = 5
        retry_delay = 5
        response_stream: Any = None
        
        for attempt in range(max_retries):
            try:
                response_stream = self.client.chat(
                    model=self.model,
                    messages=self.chat_history,
                    tools=self.tools if self.tools else None,
                    stream=True,
                    options=options_dict
                )
                break
            except Exception as e:
                if attempt < max_retries - 1:
                    logger.warning(f"Connection error to LLM backend (Attempt {attempt+1}/{max_retries}): {e}. Retrying in {retry_delay}s...")
                    print(f"{Colors.YELLOW}\n[!] Network glitch. Retrying connection to LLM... ({attempt+1}/{max_retries}){Colors.RESET}")
                    time.sleep(retry_delay)
                else:
                    raise
        
        message: dict[str, Any] = {'role': 'assistant', 'content': ''}
        tool_calls = []
        
        # Stream with retry — mid-stream disconnects no longer crash the session
        stream_success = False
        for stream_attempt in range(3):
            try:
                for chunk in response_stream:
                    chunk_msg = chunk.get('message', {})
                    if 'content' in chunk_msg and chunk_msg['content']:
                        raw_chunk = chunk_msg['content']
                        message['content'] += raw_chunk
                        display = raw_chunk.replace('\u005c\u005c', '').replace('\u005d\u005d', '')
                        display = display.replace('\u005b\u005b', '').replace('\u005d\u005d', '')
                        if display.strip():
                            print(display, end='', flush=True)
                    if 'tool_calls' in chunk_msg and chunk_msg['tool_calls']:
                        tool_calls = chunk_msg['tool_calls']
                    if chunk.get('done'):
                        if core.telemetry.session_telemetry:
                            eval_c = chunk.get('eval_count', 0)
                            prompt_c = chunk.get('prompt_eval_count', 0)
                            dur = chunk.get('eval_duration', 0)
                            core.telemetry.session_telemetry.record_llm_call(self.model, eval_c + prompt_c, dur, context="planning")
                stream_success = True
                break
            except Exception as stream_err:
                if stream_attempt < 2:
                    logger.warning(f"Stream interrupted (attempt {stream_attempt+1}/3): {stream_err}. Retrying...")
                    print(f"{Colors.YELLOW}\n[!] Stream interrupted. Retrying... ({stream_attempt+1}/3){Colors.RESET}")
                    time.sleep(3)
                    message = {'role': 'assistant', 'content': ''}
                    tool_calls = []
                    try:
                        response_stream = self.client.chat(
                            model=self.model,
                            messages=self.chat_history,
                            tools=self.tools if self.tools else None,
                            stream=True,
                            options=options_dict
                        )
                    except Exception:
                        continue
                else:
                    logger.error(f"Stream failed after 3 attempts: {stream_err}")
                    raise
        
        if not stream_success:
            raise ConnectionError("Failed to get LLM response after 3 stream attempts")

        return message, tool_calls

    def _process_tool_calls(self, tool_calls: list, message: dict, iteration: int) -> bool:
        """Validate, HITL-evaluate, and execute tool calls in parallel. Returns True if tools were executed."""
        message['tool_calls'] = tool_calls
        self.chat_history.append(message)
        self._inject_contextual_rules(message.get('content', ''))
        
        valid_tool_calls = []
        
        for tool_call in tool_calls:
            func_name = tool_call['function']['name']
            args = tool_call['function']['arguments']
            args_signature = f"{func_name}:{json.dumps(args, sort_keys=True, default=str)}"
            print(f"\n{Colors.YELLOW}[\u2699 {func_name}] -> {args}{Colors.RESET}")
            logger.info(f"TOOL CALL: {func_name} | Args: {json.dumps(args, default=str)[:500]}")
            
            # Loop detection
            if args_signature in self.recent_tool_args:
                logger.warning(f"LOOP DETECTED: {func_name}")
                print(f"{Colors.RED}[!] LOOP DETECTED: {func_name}{Colors.RESET}")
                self.chat_history.append({
                    'role': 'tool', 'name': func_name,
                    'content': f"BLOCKED: Already called {func_name} with same args. Use different approach."
                })
                self.recent_tool_args.clear()
                continue
                
            self.recent_tool_args[args_signature] = None
            if len(self.recent_tool_args) > 20:
                self.recent_tool_args.popitem(last=False)
            
            # HITL authorization gate — human approves risky operations
            hitl_decision = self.hitl.evaluate(func_name, args)
            
            if hitl_decision.action == "require_approval":
                approval = self.hitl.request_approval(func_name, args, hitl_decision.risk_level)
                if approval['decision'] == 'reject':
                    print(f"{Colors.RED}[\u2718] Operator rejected {func_name}{Colors.RESET}")
                    self.chat_history.append({
                        'role': 'tool', 'name': func_name,
                        'content': f"Operator rejected this action. Try a different approach. Reason: {hitl_decision.reason}"
                    })
                    continue
                elif approval['decision'] == 'redirect':
                    args = approval['args']
                    print(f"{Colors.GREEN}[\u21bb] Operator redirected {func_name} with modified args{Colors.RESET}")
            
            if hitl_decision.escalated:
                print(f"{Colors.YELLOW}[\u26a1] Risk escalated to {hitl_decision.risk_level}: {hitl_decision.reason}{Colors.RESET}")
                
            if func_name not in self.skills:
                print(f"{Colors.RED}[!] Unknown skill: {func_name}{Colors.RESET}")
                logger.error(f"HALLUCINATED SKILL: {func_name}")
                self.chat_history.append({'role': 'tool', 'name': func_name, 'content': f"Error: Skill {func_name} not found."})
                continue
                
            valid_tool_calls.append((func_name, args))
        
        # Execute all valid tool calls in parallel (supports both sync and async tools)
        if valid_tool_calls:
            def run_tool(func_name, args):
                """Execute a single tool with pre/post hooks. Supports async tools via asyncio."""
                func = self.skills[func_name]
                if hasattr(core.hooks, 'pre_execute'):
                    args = core.hooks.pre_execute(func_name, args)
                try:
                    if inspect.iscoroutinefunction(func):
                        # Async tool — run in a new event loop
                        result = asyncio.run(func(**args))
                    else:
                        result = func(**args)
                    tool_result = str(result)
                except Exception as e:
                    tool_result = f"Command execution failed: {e}"
                    
                if hasattr(core.hooks, 'post_execute'):
                    tool_result = core.hooks.post_execute(func_name, args, tool_result, self)
                return tool_result

            results = []
            with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(valid_tool_calls), 5)) as executor:
                future_to_tool = {executor.submit(run_tool, fname, fargs): (fname, fargs) for fname, fargs in valid_tool_calls}
                for future in concurrent.futures.as_completed(future_to_tool):
                    fname, fargs = future_to_tool[future]
                    try:
                        res = future.result()
                        results.append((fname, fargs, res))
                    except Exception as exc:
                        results.append((fname, fargs, f"Execution generated an exception: {exc}"))

            # Process results and append to chat history
            for func_name, args, tool_result in results:
                print(f"{Colors.GRAY}[*] Completed '{func_name}'{Colors.RESET}")
                logger.info(f"TOOL RESULT ({func_name}): {tool_result[:500]}")
                
                args_preview = json.dumps(args, default=str)[:100]
                self.tried_actions.append(f"{func_name}({args_preview})")
                
                # Failure detection
                error_keywords = [
                    "traceback", "syntaxerror", "command not found",
                    "connection refused", "fatal error", "failed to load",
                    "unknown command", "no results", "module not found"
                ]
                if any(err in tool_result.lower() for err in error_keywords) or tool_result.startswith("Command execution failed"):
                    self.consecutive_failures += 1
                    if self.consecutive_failures >= 2:
                        tool_result += "\n\n[SYSTEM OVERRIDE: CONSECUTIVE FAILURES]\nFailure recorded. PIVOT to different tool/methodology."
                        self.consecutive_failures = 0
                else:
                    self.consecutive_failures = max(0, self.consecutive_failures - 1)
                
                # Truncate large outputs
                max_len = 15000 if self.mode == "cloud" else 6000
                if len(tool_result) > max_len:
                    tool_result = tool_result[:max_len] + "\n...[TRUNCATED - USE GREP OR OUTPUT FILES]..."
                    
                self.chat_history.append({
                    'role': 'tool', 
                    'content': tool_result,
                    'name': func_name
                })

        if iteration % 3 == 0 and self.tried_actions:
            recent_tried = self.tried_actions[-30:]
            tried_text = "\n".join(f"  {i+1}. {a}" for i, a in enumerate(recent_tried))
            self.chat_history.append({
                'role': 'system',
                'content': f"ALREADY TRIED (do NOT repeat):\n{tried_text}"
            })
            logger.info(f"TRIED-LIST INJECTED: {len(recent_tried)} entries")
        
        return True

    def _handle_final_response(self, message: dict) -> str | None:
        """Handle a final (non-tool-call) response from the LLM.
        
        Returns the response string if the session is complete, or None if
        the response was empty/thinking-only and the loop should continue.
        """
        self.chat_history.append(message)

        content_str = message.get('content', '')
        has_visible_content = any(c.isprintable() and ord(c) > 32 for c in content_str)
        has_tool_calls = bool(message.get('tool_calls'))

        if not has_visible_content and not has_tool_calls:
            # No real content — check if it's just thinking tokens
            think_tokens = ['\u005c\u005c', '\u005d\u005d']
            if any(token in content_str for token in think_tokens):
                print(f"{Colors.YELLOW}[!] Filtered invisible thinking tokens{Colors.RESET}")
                logger.info(f"Filtered thinking tokens: {content_str}")
                self.chat_history.pop()  # Remove the empty assistant message
                return None
            else:
                # Truly empty — nudge for a retry
                print(f"{Colors.YELLOW}[!] Empty response. Retrying...{Colors.RESET}")
                self.chat_history.append({
                    'role': 'user',
                    'content': 'Previous response empty. Respond with tool call or answer.'
                })
                return None

        # Success — has visible content, return final response
        logger.info("SESSION COMPLETED")
        return content_str

    def chat(self, user_input: str, max_iterations: int = 100) -> str:
        """Execute ReAct loop for user prompt."""
        self._inject_kb_context(user_input)
        self.chat_history.append({'role': 'user', 'content': user_input})
        logger.info(f"USER INPUT: {user_input[:200]}")
        
        iteration = 0
        while True:
            if iteration >= max_iterations:
                logger.warning("MAX ITERATIONS REACHED")
                print(f"\n{Colors.YELLOW}[!] Autonomy limit reached ({max_iterations} iterations).{Colors.RESET}")
                try:
                    choice = input(f"{Colors.CYAN}[?] Continue for another 100 iterations? (y/N) > {Colors.RESET}").strip().lower()
                    if choice in ('y', 'yes'):
                        max_iterations += 100
                    else:
                        return "[Max iterations reached. Session terminated by operator.]"
                except (KeyboardInterrupt, EOFError):
                    return "[Max iterations reached. Session terminated by operator.]"
            
            iteration += 1
            try:
                print(f"\n{Colors.GRAY}[*] Processing... {Colors.CYAN}", end='', flush=True)
                self._compress_history()
                
                context_limit = 32768 if self.mode == "cloud" else 8192
                temp = 0.8 if self.mode == "cloud" else None
                
                options_dict: dict[str, Any] = {'num_ctx': context_limit}
                if temp is not None:
                    options_dict['temperature'] = temp

                self._inject_cloud_memory()

                message, tool_calls = self._stream_response(options_dict)
                print(f"{Colors.RESET}")

                # After getting model response, parse text-based tool calls
                parsed_tool_calls = self.parse_almost_json_tool_calls(message['content'])
                if parsed_tool_calls and not tool_calls:
                    tool_calls = parsed_tool_calls
                    print(f"{Colors.GREEN}[*] Parsed text-based tool call{Colors.RESET}")
                
                # Handle empty responses
                if not message['content'].strip() and not tool_calls:
                    logger.warning(f"EMPTY RESPONSE on iteration {iteration}")
                    print(f"{Colors.YELLOW}[!] Empty response. Retrying...{Colors.RESET}")
                    self.chat_history.append({
                        'role': 'user',
                        'content': 'Previous response empty. Respond with tool call or answer.'
                    })
                    continue
                
                # Process tool calls
                if tool_calls:
                    self._process_tool_calls(tool_calls, message, iteration)
                    continue
                    
                else:
                    # Final response handling
                    result = self._handle_final_response(message)
                    if result is not None:
                        return result
                    continue
                    
            except KeyboardInterrupt:
                print(f"\n{Colors.RED}[!] SESSION INTERRUPTED{Colors.RESET}")
                logger.info("SESSION INTERRUPTED BY USER")
                self.save_state()
                return "[Session terminated by user]"
            except Exception as e:
                logger.error(f"CHAT ERROR: {str(e)}", exc_info=True)
                print(f"{Colors.RED}[!] Error during chat: {e}{Colors.RESET}")
                self.save_state()
                return f"[Error occurred: {e}]"
