"""
Python specific tool extensions.
"""
import re
from tools.local_ops import execute_command

def install_python_package(package_name: str) -> str:
    """Installs a python package using pip.
    
    Validates the package name to prevent shell injection — rejects names
    containing shell metacharacters (;, &, |, &&, ||, $, `, etc.).
    """
    if not re.match(r'^[a-zA-Z0-9._-]+$', package_name):
        return f"[ERROR] Invalid package name '{package_name}'. Only alphanumeric characters, dots, hyphens, and underscores are allowed."
    cmd = f"pip install {package_name}"
    return execute_command(cmd, timeout=120)
