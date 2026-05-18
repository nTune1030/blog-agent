"""
Advanced web scanning and manipulation tools.

All web tools default to verify=True (SSL certificate verification enabled).
Setting verify=False requires HITL HIGH risk approval before execution.

Includes both synchronous (requests) and asynchronous (aiohttp) versions
of fetch and fuzz operations. Async versions are prefixed with `async_` and
can be used by the agent for concurrent I/O when multiple web calls are needed.
"""
import requests
import asyncio
import json
from bs4 import BeautifulSoup
from typing import Any
import urllib3
import core.telemetry
from core.config import logger, Colors

# Disable insecure request warnings when testing direct IPs without certificates
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Global Session to persist Cookies and CSRF states across multi-turn agent interactions
GLOBAL_WEB_SESSION = requests.Session()

# Lazy-initialized aiohttp session for async operations
_async_session = None


def _get_async_session():
    """Lazy-initialize the aiohttp client session for async web operations."""
    global _async_session
    if _async_session is None or _async_session.closed:
        try:
            import aiohttp
            _async_session = aiohttp.ClientSession()
        except ImportError:
            raise ImportError("aiohttp is required for async web operations. Install with: pip install aiohttp")
    return _async_session


async def close_async_session():
    """Close the async aiohttp session. Call during shutdown."""
    global _async_session
    if _async_session and not _async_session.closed:
        await _async_session.close()
        _async_session = None


def reset_web_session() -> str:
    """
    Clears all saved cookies, cache, and authentication states from the global web testing session.
    Use this when switching between authenticated user profiles or testing unauthenticated attack vectors.
    """
    global GLOBAL_WEB_SESSION
    GLOBAL_WEB_SESSION.cookies.clear()
    
    if core.telemetry.session_telemetry:
        core.telemetry.session_telemetry.record_command_execution('reset_web_session', success=True)
        
    return "[INFO] Global Web state flushed. Authentication cookies cleared successfully."


def fetch_web_content(
    url: str,
    method: str = "GET",
    data: dict | None = None,
    headers: dict | None = None,
    cookies: dict | None = None,
    strip_html: bool = True,
    use_stateful_session: bool = True,
    verify: bool = True
) -> str:
    """
    Fetch a webpage natively and return the parsed text content.
    `use_stateful_session`: Uses a persistent background proxy to maintain authentication cookies across tools!
    `strip_html`: Uses BeautifulSoup to remove DOM trees and return pure readable text, drastically saving tokens.
    `verify`: Verify SSL certificates. Defaults to True. Set to False only for testing (HITL will require approval).
    """
    try:
        executor = GLOBAL_WEB_SESSION if use_stateful_session else requests
        
        response = executor.request(
            method=method.upper(),
            url=url,
            headers=headers or {},
            cookies=cookies or {},
            data=data,
            timeout=15,
            verify=verify
        )
        
        if core.telemetry.session_telemetry:
            core.telemetry.session_telemetry.record_command_execution('fetch_web_content', success=True)
            
        if strip_html:
            soup = BeautifulSoup(response.text, 'html.parser')
            # Extract text preserving newline structure
            text_content = soup.get_text(separator='\n', strip=True)
            
            # Compress excessive newlines
            lines = [line.strip() for line in text_content.splitlines() if line.strip()]
            final_text = '\n'.join(lines)
            
            return f"[Status: {response.status_code} | Length: {len(response.text)}]\n\n{final_text}"
        else:
            return f"[Status: {response.status_code} | Length: {len(response.text)}]\n\n{response.text}"
            
    except Exception as e:
        logger.warning(f"Fetch web content failed for {url}: {e}")
        if core.telemetry.session_telemetry:
            core.telemetry.session_telemetry.record_command_execution('fetch_web_content', success=False)
        return f"[ERROR] Request failed: {str(e)}"


async def async_fetch_web_content(
    url: str,
    method: str = "GET",
    data: dict | None = None,
    headers: dict | None = None,
    cookies: dict | None = None,
    strip_html: bool = True,
    verify: bool = True
) -> str:
    """
    Async version of fetch_web_content using aiohttp for concurrent I/O.
    
    Fetches a webpage asynchronously and returns the parsed text content.
    Uses a shared aiohttp.ClientSession for connection pooling.
    Does not support stateful session (cookie persistence) — use sync version for that.
    `verify`: Verify SSL certificates. Defaults to True.
    """
    try:
        import aiohttp
        
        session = _get_async_session()
        kwargs: dict[str, Any] = {
            "method": method.upper(),
            "url": url,
            "headers": headers or {},
            "timeout": aiohttp.ClientTimeout(total=15),
        }
        if data:
            kwargs["data"] = json.dumps(data)
            if headers is None:
                kwargs["headers"] = {"Content-Type": "application/json"}
        if cookies:
            kwargs["cookies"] = cookies
        if not verify:
            kwargs["ssl"] = False
            
        async with session.request(**kwargs) as response:
            text = await response.text()
            status = response.status
            
            if core.telemetry.session_telemetry:
                core.telemetry.session_telemetry.record_command_execution('async_fetch_web_content', success=True)
            
            if strip_html:
                soup = BeautifulSoup(text, 'html.parser')
                text_content = soup.get_text(separator='\n', strip=True)
                lines = [line.strip() for line in text_content.splitlines() if line.strip()]
                final_text = '\n'.join(lines)
                return f"[Status: {status} | Length: {len(text)}]\n\n{final_text}"
            else:
                return f"[Status: {status} | Length: {len(text)}]\n\n{text}"
                
    except ImportError:
        return "[ERROR] aiohttp not installed. Install with: pip install aiohttp"
    except Exception as e:
        logger.warning(f"Async fetch failed for {url}: {e}")
        if core.telemetry.session_telemetry:
            core.telemetry.session_telemetry.record_command_execution('async_fetch_web_content', success=False)
        return f"[ERROR] Async request failed: {str(e)}"


def fuzz_web_endpoint(
    url: str,
    payloads: list,
    fuzz_param: str | None = None,
    method: str = "GET",
    headers: dict | None = None,
    cookies: dict | None = None,
    use_stateful_session: bool = True,
    verify: bool = True
) -> str:
    """
    Rapid, native programmatic web fuzzer designed to iterate through payload arrays asynchronously.
    For GET requests: `url` must contain a `FUZZ` placeholder (e.g. "http://target/v.php?id=FUZZ").
    For POST requests: provide `fuzz_param` mapping to the data dictionary key to inject payloads into.
    Filters out normal responses and heavily condenses iterations into a single anomaly report.
    `use_stateful_session`: Relies on previously obtained cookies (from `fetch_web_content` logins) automatically.
    `verify`: Verify SSL certificates. Defaults to True. Set to False only for testing (HITL will require approval).
    """
    if not payloads or not isinstance(payloads, list):
        return "[ERROR] Payload target must be a populated array/list of strings."

    # Utilize our global persistence if asked
    session = GLOBAL_WEB_SESSION if use_stateful_session else requests.Session()
    results = []
    baseline_len = None
    
    try:
        # 1. Establish Baseline Variance using a ghost payload
        baseline_resp = session.request(
            method=method.upper(),
            url=url.replace("FUZZ", "non_existent_baseline_1337"),
            headers=headers or {},
            cookies=cookies or {},
            timeout=10,
            verify=verify
        )
        baseline_len = len(baseline_resp.text)
        baseline_status = baseline_resp.status_code
        
        # 2. Iterate array natively
        for payload in payloads:
            target_url = url
            data = None
            
            p_str = str(payload)
            
            if method.upper() == "GET":
                target_url = url.replace("FUZZ", p_str)
            elif method.upper() == "POST" and fuzz_param:
                data = {fuzz_param: p_str}
                
            resp = session.request(
                method=method.upper(),
                url=target_url,
                data=data,
                headers=headers or {},
                cookies=cookies or {},
                timeout=10,
                verify=verify
            )
            
            current_len = len(resp.text)
            
            # Anomaly trigger: Status code shifts or Length drifts heavily by >2%
            if baseline_len == 0 or abs(current_len - baseline_len) / max(1, baseline_len) > 0.02 or resp.status_code != baseline_status:
                results.append({
                    "payload": p_str,
                    "status": resp.status_code,
                    "length": current_len
                })
        
        if core.telemetry.session_telemetry:
            core.telemetry.session_telemetry.record_command_execution('fuzz_web_endpoint', success=True)
            
        if not results:
            return f"[FUZZ COMPLETE] Baseline Length: {baseline_len}. No anomalies detected from {len(payloads)} payloads."
            
        output = f"[FUZZ COMPLETE] Baseline Length: {baseline_len} / Baseline Status: {baseline_status}\nIdentified {len(results)} Anomalous Responses:\n"
        for r in results:
            output += f" - Payload injected: '{r['payload']}'  -->  Status: {r['status']}  |  Length: {r['length']}\n"
        return output

    except Exception as e:
        logger.warning(f"Fuzzing failed against {url}: {e}")
        if core.telemetry.session_telemetry:
            core.telemetry.session_telemetry.record_command_execution('fuzz_web_endpoint', success=False)
        return f"[ERROR] Fuzzing sequence failed: {str(e)}"


async def async_fuzz_web_endpoint(
    url: str,
    payloads: list,
    fuzz_param: str | None = None,
    method: str = "GET",
    headers: dict | None = None,
    cookies: dict | None = None,
    verify: bool = True
) -> str:
    """
    Async version of fuzz_web_endpoint using aiohttp for concurrent I/O.
    
    Sends all fuzz payloads concurrently using asyncio.gather() for much faster
    fuzzing than the synchronous sequential version. Does not support stateful session.
    `verify`: Verify SSL certificates. Defaults to True.
    """
    if not payloads or not isinstance(payloads, list):
        return "[ERROR] Payload target must be a populated array/list of strings."
    
    try:
        import aiohttp
    except ImportError:
        return "[ERROR] aiohttp not installed. Install with: pip install aiohttp"
    
    try:
        session = _get_async_session()
        ssl_context = None if verify else False
        results = []
        
        # 1. Establish baseline
        baseline_url = url.replace("FUZZ", "non_existent_baseline_1337")
        baseline_kwargs: dict[str, Any] = {
            "method": method.upper(),
            "url": baseline_url,
            "headers": headers or {},
            "timeout": aiohttp.ClientTimeout(total=10),
        }
        if cookies:
            baseline_kwargs["cookies"] = cookies
        if not verify:
            baseline_kwargs["ssl"] = ssl_context
            
        async with session.request(**baseline_kwargs) as baseline_resp:
            baseline_text = await baseline_resp.text()
            baseline_len = len(baseline_text)
            baseline_status = baseline_resp.status
        
        # 2. Build all request tasks
        async def _fuzz_single(payload_item) -> dict | None:
            p_str = str(payload_item)
            target_url = url
            data = None
            
            if method.upper() == "GET":
                target_url = url.replace("FUZZ", p_str)
            elif method.upper() == "POST" and fuzz_param:
                data = json.dumps({fuzz_param: p_str})
            
            req_kwargs: dict[str, Any] = {
                "method": method.upper(),
                "url": target_url,
                "headers": headers or {},
                "timeout": aiohttp.ClientTimeout(total=10),
            }
            if data:
                req_kwargs["data"] = data
                if headers is None:
                    req_kwargs["headers"] = {"Content-Type": "application/json"}
            if cookies:
                req_kwargs["cookies"] = cookies
            if not verify:
                req_kwargs["ssl"] = ssl_context
                
            try:
                async with session.request(**req_kwargs) as resp:
                    text = await resp.text()
                    current_len = len(text)
                    if baseline_len == 0 or abs(current_len - baseline_len) / max(1, baseline_len) > 0.02 or resp.status != baseline_status:
                        return {"payload": p_str, "status": resp.status, "length": current_len}
                    return None
            except Exception:
                return None
        
        # 3. Execute all payloads concurrently
        fuzz_results = await asyncio.gather(*[_fuzz_single(p) for p in payloads])
        results = [r for r in fuzz_results if r is not None]
        
        if core.telemetry.session_telemetry:
            core.telemetry.session_telemetry.record_command_execution('async_fuzz_web_endpoint', success=True)
        
        if not results:
            return f"[FUZZ COMPLETE] Baseline Length: {baseline_len}. No anomalies detected from {len(payloads)} payloads."
        
        output = f"[FUZZ COMPLETE] Baseline Length: {baseline_len} / Baseline Status: {baseline_status}\nIdentified {len(results)} Anomalous Responses:\n"
        for r in results:
            output += f" - Payload injected: '{r['payload']}'  -->  Status: {r['status']}  |  Length: {r['length']}\n"
        return output
        
    except ImportError:
        return "[ERROR] aiohttp not installed. Install with: pip install aiohttp"
    except Exception as e:
        logger.warning(f"Async fuzzing failed against {url}: {e}")
        if core.telemetry.session_telemetry:
            core.telemetry.session_telemetry.record_command_execution('async_fuzz_web_endpoint', success=False)
        return f"[ERROR] Async fuzzing failed: {str(e)}"
