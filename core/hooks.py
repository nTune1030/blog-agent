"""
Execution interception hooks for data modification and HITL awareness.

Pre-execute: Can modify args before tool execution. Use to annotate or transform args.
Post-execute: Can analyze or modify tool output before it returns to the LLM.
"""
from typing import Any

def pre_execute(tool_name: str, args: dict) -> dict:
    """
    Hook to modify or annotate tool arguments before execution.
    Called BEFORE HITL evaluation — useful for normalizing args.
    Return the (possibly modified) args dictionary.
    """
    return args

def post_execute(tool_name: str, args: dict, result: str, agent: Any) -> str:
    """
    Hook to analyze tool output after execution.
    Useful for semantic loop detection or auto-correction logic.
    Return the (possibly modified) result string.
    """
    return result
