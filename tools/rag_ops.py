"""
RAG and web searching operations.

ChromaDB and Ollama clients are lazily initialized on first use to prevent
import-time connection errors from crashing the entire plugin loader.
"""
import os
import time
import hashlib
import json
from core.config import MEMORY_DIR, APP_CONFIG, logger
import core.telemetry

# Lazy-initialized singletons — created on first access, not at import time
_rag_chroma_client = None
_rag_collection = None
_ollama_client = None
_syntax_model = None

try:
    from duckduckgo_search import DDGS
    WEB_SEARCH_AVAILABLE = True
except ImportError:
    WEB_SEARCH_AVAILABLE = False
    logger.warning("duckduckgo_search not installed. Web search fallback disabled.")

_validation_cache = {}
CACHE_TTL = 3600  # 1 hour


def _get_chroma_collection():
    """Lazily initialize and return the ChromaDB knowledge_cache collection."""
    global _rag_chroma_client, _rag_collection
    if _rag_collection is None:
        import chromadb
        _rag_chroma_client = chromadb.PersistentClient(path=MEMORY_DIR)
        _rag_collection = _rag_chroma_client.get_or_create_collection(name="knowledge_cache")
    return _rag_collection


def _get_ollama_client():
    """Lazily initialize and return the Ollama client for syntax validation."""
    global _ollama_client
    if _ollama_client is None:
        from ollama import Client
        _ollama_client = Client()
    return _ollama_client


def _get_syntax_model():
    """Lazily resolve the syntax validation model name from config."""
    global _syntax_model
    if _syntax_model is None:
        _syntax_model = APP_CONFIG.get("agent", {}).get("local_model", "qwen-coder:latest")
    return _syntax_model


def seed_knowledge_base(data_dict: dict) -> None:
    """
    Generic tool to seed ChromaDB with knowledge for the specific agent.
    Expected format: {"item_name": "detailed knowledge string"}
    """
    collection = _get_chroma_collection()
    docs, metas, ids = [], [], []
    for item_id, content in data_dict.items():
        docs.append(content)
        metas.append({"source": "seed", "type": "knowledge"})
        ids.append(f"knowledge_{item_id}")
    
    if docs:
        collection.add(documents=docs, metadatas=metas, ids=ids)
        print(f"[*] Seeded {len(docs)} items into local knowledge base.")

def search_github(query: str) -> str:
    """Search GitHub for code examples, documentation, or repositories."""
    if not WEB_SEARCH_AVAILABLE:
        return "Web search is currently disabled."
    
    try:
        search_query = f"{query} site:github.com"
        results = DDGS().text(search_query, max_results=5)
        
        github_context = ""
        for result in results:
            title = result.get('title', '')
            url = result.get('href', '')
            snippet = result.get('body', '')
            github_context += f"GitHub Result: {title}\nURL: {url}\nSnippet: {snippet}\n\n"
            
        return github_context[:2000] if github_context else "No GitHub results found."
    except Exception as e:
        logger.warning(f"GitHub search failed: {e}")
        return f"Search failed: {e}"

def search_stackoverflow(query: str) -> str:
    """Search StackOverflow to resolve specific errors or technical issues."""
    if not WEB_SEARCH_AVAILABLE:
        return "Web search is currently disabled."
    
    try:
        search_query = f"{query} site:stackoverflow.com"
        results = DDGS().text(search_query, max_results=5)
        
        so_context = ""
        for result in results:
            title = result.get('title', '')
            url = result.get('href', '')
            snippet = result.get('body', '')
            so_context += f"StackOverflow: {title}\nURL: {url}\nSolution: {snippet[:300]}\n\n"
            
        return so_context[:2000] if so_context else "No StackOverflow results found."
    except Exception as e:
        logger.warning(f"StackOverflow search failed: {e}")
        return f"Search failed: {e}"

def deep_research(topic: str) -> str:
    """Comprehensive research function that queries general web, GitHub, and StackOverflow."""
    if not WEB_SEARCH_AVAILABLE:
        return "Web search is currently disabled."
    
    research_results = []
    
    # 1. General Search
    try:
        gen_results = DDGS().text(topic, max_results=3)
        gen_context = "\n".join([f"- {r.get('title', '')}: {r.get('body', '')}" for r in gen_results])
        if gen_context:
            research_results.append("### General Guidance ###\n" + gen_context)
    except Exception as e:
        logger.warning(f"General research failed: {e}")

    # 2. StackOverflow
    so_context = search_stackoverflow(topic)
    if "No StackOverflow results" not in so_context and "Search failed" not in so_context:
        research_results.append("### StackOverflow Context ###\n" + so_context)

    # 3. GitHub
    gh_context = search_github(topic)
    if "No GitHub results" not in gh_context and "Search failed" not in gh_context:
        research_results.append("### GitHub Context ###\n" + gh_context)
        
    summary = "\n\n".join(research_results)
    return summary[:4000] if summary else "No comprehensive research results found."

def _search_web_for_syntax(command_or_tool: str) -> str:
    """Fallback web search specifically for command syntax."""
    if not WEB_SEARCH_AVAILABLE:
        return ""
    try:
        results = DDGS().text(f"{command_or_tool} command syntax terminal usage examples", max_results=3)
        context = ""
        for r in results:
            context += f"- {r.get('body', '')}\n"
        if context:
            collection = _get_chroma_collection()
            collection.add(
                documents=[f"Web Search Cache for {command_or_tool}:\n{context}"],
                metadatas=[{"tool": command_or_tool, "type": "web_cache"}],
                ids=[f"web_cache_{hashlib.md5(command_or_tool.encode()).hexdigest()}_{int(time.time())}"]
            )
            if core.telemetry.session_telemetry:
                core.telemetry.session_telemetry.record_web_search(success=True)
        else:
            if core.telemetry.session_telemetry:
                core.telemetry.session_telemetry.record_web_search(success=False)
        return context
    except Exception as e:
        logger.warning(f"Web search failed for {command_or_tool}: {e}")
        if core.telemetry.session_telemetry:
            core.telemetry.session_telemetry.record_web_search(success=False)
        return ""

def validate_and_enhance_command(command: str) -> dict:
    """
    Validates command syntax against local knowledge base and web search.
    Returns dict with confidence and suggested syntax.
    """
    cache_key = hashlib.md5(command.encode()).hexdigest()
    if cache_key in _validation_cache:
        cached_result, timestamp = _validation_cache[cache_key]
        if time.time() - timestamp < CACHE_TTL:
            return cached_result
            
    base_tool = command.split()[0] if command.split() else ""
    
    # 1. Query Local ChromaDB
    collection = _get_chroma_collection()
    local_results = collection.query(
        query_texts=[f"How to use {base_tool} and valid syntax for {command}"],
        n_results=2
    )
    
    context = ""
    if local_results and local_results['documents'] and local_results['documents'][0]:
        context = "\n".join(local_results['documents'][0])
        
    # 2. Web Fallback if local context is extremely sparse
    if len(context) < 50 and WEB_SEARCH_AVAILABLE:
        context += _search_web_for_syntax(base_tool)
        
    if not context:
        return {'valid': True, 'confidence': 0.0, 'suggested_syntax': command}
        
    # 3. LLM Syntax Checking
    prompt = f"""You are a strict technical command syntax validator.
Review the following command: `{command}`

Use this Documentation Context to verify if the syntax, flags, and usage are correct:
{context}

Response Format exactly as JSON:
{{
   "valid": boolean,
   "confidence": float between 0.0 and 1.0,
   "suggested_syntax": "string of the fixed command, or original if valid",
   "reason": "short explanation of the fix"
}}
You must return valid JSON only. If the syntax is wildly wrong, hallucinated, or you have low confidence, set valid to false and suggested_syntax to a corrected format. If valid, set valid to true and return the original command.
"""
    try:
        client = _get_ollama_client()
        model = _get_syntax_model()
        response = client.chat(
            model=model,
            messages=[{'role': 'user', 'content': prompt}],
            format='json'
        )
        result = json.loads(response['message']['content'])
        
        final_out = {
            'valid': result.get('valid', True),
            'confidence': float(result.get('confidence', 0.0)),
            'suggested_syntax': result.get('suggested_syntax', command),
            'reason': result.get('reason', '')
        }
        
        # If confidence is still very low, do a targeted deep dive (auto-correction via SO)
        if final_out['confidence'] < 0.6 and WEB_SEARCH_AVAILABLE:
            so_context = search_stackoverflow(f"how to run {base_tool} {command} syntax error")
            if "No StackOverflow results" not in so_context:
                final_out['reason'] += f" [Research Note: {so_context[:200]}]"
                
        _validation_cache[cache_key] = (final_out, time.time())
        if core.telemetry.session_telemetry:
            was_corrected = (final_out['confidence'] > 0.9 and not final_out['valid'])
            core.telemetry.session_telemetry.record_validation(final_out['confidence'], was_corrected)
        return final_out
        
    except Exception as e:
        logger.warning(f"Syntax validation failed internally: {e}")
        return {'valid': True, 'confidence': 0.0, 'suggested_syntax': command}
