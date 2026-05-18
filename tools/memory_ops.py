"""
Vector database memory operations.
"""
import os
import json
from datetime import datetime
from core.config import LOGS_DIR, MEMORY_DIR, logger

def save_note(note: str, filename: str = None) -> str:
    """Save textual note permanently"""
    if not filename:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"note_{timestamp}.txt"
    filepath = os.path.join(LOGS_DIR, filename)
    try:
        with open(filepath, 'w') as f:
            f.write(note)
        logger.info(f"NOTE SAVED: {filename}")
        return f"[Saved to {filepath}]"
    except Exception as e:
        return f"[ERROR saving note: {e}]"

def report_finding(ip: str, finding: str, command: str) -> str:
    """Report a vulnerability finding with the command used"""
    filename = f"report_{ip.replace('.', '_')}.txt"
    filepath = os.path.join(LOGS_DIR, filename)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = f"[{timestamp}]\nFINDING: {finding}\nCOMMAND: {command}\n{'-'*40}\n"
    try:
        with open(filepath, 'a') as f:
            f.write(entry)
        logger.info(f"Report appended for {ip}")
        return f"[Finding for {ip} appended to {filename}]"
    except Exception as e:
        return f"[ERROR appending report: {e}]"

def record_learning(topic: str, insight: str) -> str:
    """Record new learning"""
    kb_file = os.path.join(MEMORY_DIR, "knowledge_base.json")
    entry = {
        "topic": topic,
        "insight": insight,
        "timestamp": datetime.now().isoformat()
    }
    try:
        data = []
        if os.path.exists(kb_file):
            with open(kb_file, 'r') as f:
                try:
                    data = json.load(f)
                except json.JSONDecodeError:
                    data = []
        # Dedup: skip if identical topic+insight already exists
        if any(e['topic'] == topic and e['insight'] == insight for e in data):
            return "[DUPLICATE] Already recorded."
        data.append(entry)
        with open(kb_file, 'w') as f:
            json.dump(data, f, indent=2)
        logger.info(f"LEARNING RECORDED: {topic}")
        return f"[Learning recorded under '{topic}']"
    except Exception as e:
        return f"[ERROR recording learning: {e}]"

def smart_search_learnings(query: str) -> str:
    """Search prior learnings"""
    kb_file = os.path.join(MEMORY_DIR, "knowledge_base.json")
    if not os.path.exists(kb_file):
        return "[No knowledge base found]"
    try:
        with open(kb_file, 'r') as f:
            data = json.load(f)
        matches = [entry for entry in data if query.lower() in entry['topic'].lower() or query.lower() in entry['insight'].lower()]
        if not matches:
            return f"[No matches found for '{query}']"
        formatted = "\n".join([f"- {m['topic']}: {m['insight'][:200]}" for m in matches[:5]])
        return f"Found {len(matches)} entries:\n{formatted}"
    except Exception as e:
        return f"[ERROR searching learnings: {e}]"
