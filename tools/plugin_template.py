"""
Plugin Template
----------------
Copy this file and rename it (e.g., 'my_custom_tool.py') to create a new tool.
The bootloader will automatically scan this file and register any functions you define.

HITL Risk Classification:
Every tool is automatically classified with a risk level (LOW/MEDIUM/HIGH/CRITICAL).
HIGH and CRITICAL tools require operator approval before execution.
Use the @hitl_risk decorator to explicitly declare your tool's risk level.
"""

import os
import logging
from core.config import logger
from core.hitl import hitl_risk, RiskLevel

# -------------------------------------------------------------------------
# Tool Definitions
# -------------------------------------------------------------------------
# Rules for the LLM to understand your tool:
# 1. Provide a clear Docstring (this becomes the LLM's instruction manual).
# 2. Use Type Hints for all parameters.
# 3. Return a string (or an object that safely casts to string) so the LLM can read the result.
# 4. Optionally, use @hitl_risk() to declare the tool's risk level for HITL authorization.

# Example: LOW risk tool (auto-approved, no operator interaction needed)
def example_calculator_tool(a: int, b: int, operation: str) -> str:
    """
    Performs basic arithmetic operations.
    Valid operations are: 'add', 'subtract', 'multiply', 'divide'.
    """
    try:
        if operation == "add":
            return f"Result: {a + b}"
        elif operation == "subtract":
            return f"Result: {a - b}"
        elif operation == "multiply":
            return f"Result: {a * b}"
        elif operation == "divide":
            if b == 0:
                return "Error: Cannot divide by zero."
            return f"Result: {a / b}"
        else:
            return f"Error: Unknown operation '{operation}'"
            
    except Exception as e:
        logger.error(f"Calculator failed: {e}")
        return f"Execution failed: {e}"


# Example: HIGH risk tool (requires operator approval before execution)
@hitl_risk(RiskLevel.HIGH)
def example_dangerous_tool(target: str, action: str) -> str:
    """
    Example of a tool that requires HITL approval before execution.
    The @hitl_risk decorator ensures the operator is prompted before this runs.
    """
    return f"Would perform {action} on {target}"


def _hidden_helper_function():
    """
    Functions starting with an underscore (_) are ignored by the bootloader.
    Use these for internal logic that the LLM shouldn't see or call directly.
    """
    pass
