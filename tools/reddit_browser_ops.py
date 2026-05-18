"""
Reddit browser posting tools — Playwright-based fallback for posting to Reddit.

When Reddit API access (PRAW) is not available or not approved, these tools
use browser automation to post to Reddit through the web UI. This bypasses
the API registration requirement entirely.

Uses Playwright with playwright-stealth for bot detection evasion. Runs in
visible browser mode (headless=False) because Reddit aggressively blocks
headless browsers with JS challenges.

Two modes:
  1. Semi-automatic (recommended): Opens visible browser, logs in, fills the
     form, but waits for you to click "Post" — you stay in control.
  2. Fully automatic: Logs in and posts without manual intervention.
     Requires explicit YES confirmation (CRITICAL HITL risk).

HITL Risk Classification:
- post_to_reddit_browser: CRITICAL (irreversible public action)
- open_reddit_post_form: HIGH (opens browser, fills form, waits for user)
- check_reddit_browser_ready: LOW (read-only check)
"""

import json
import os
import asyncio
from typing import Optional

from core.config import Colors, logger
from core.hitl import hitl_risk, RiskLevel


def _load_credentials() -> dict:
    """Load Reddit credentials from promotion_profile.json."""
    profile_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "promotion_profile.json"
    )
    try:
        with open(profile_path, 'r', encoding='utf-8') as f:
            profile = json.load(f)
        return profile.get("api_credentials", {}).get("reddit", {})
    except Exception as e:
        logger.error(f"Failed to load credentials: {e}")
        return {}


def _run_async(coro):
    """Run an async coroutine from sync code."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                return pool.submit(asyncio.run, coro).result()
        return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)


async def _create_browser(headless: bool = False):
    """Create a Playwright browser instance with stealth.

    Args:
        headless: If True, run in headless mode. Default False because
                  Reddit blocks headless browsers.

    Returns:
        Tuple of (playwright_instance, browser, context, page)
    """
    from playwright.async_api import async_playwright

    try:
        from playwright_stealth import Stealth
        stealth_config = Stealth()
        has_stealth = True
    except ImportError:
        stealth_config = None
        has_stealth = False
        logger.warning("playwright-stealth not installed. Run: pip install playwright-stealth")

    pw = await async_playwright().start()

    # Hook stealth into the Playwright context so ALL browsers/pages
    # launched from this instance automatically get stealth applied.
    # This is the recommended v2 API approach (replaces apply/apply_async).
    if has_stealth and stealth_config:
        stealth_config.hook_playwright_context(pw)
        logger.info("Hooked playwright-stealth into browser context")

    browser = await pw.chromium.launch(
        headless=headless,
        args=[
            '--disable-blink-features=AutomationControlled',
            '--no-sandbox',
            '--disable-dev-shm-usage',
        ]
    )

    context = await browser.new_context(
        viewport={'width': 1280, 'height': 800},
        user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36',
        locale='en-US',
        timezone_id='America/New_York',
    )

    page = await context.new_page()

    # Belt-and-suspenders: also apply stealth directly to the page.
    # hook_playwright_context patches the browser launch, but this ensures
    # stealth scripts are injected even if hooking didn't fully take effect.
    if has_stealth and stealth_config:
        await stealth_config.apply_stealth_async(page)
        logger.info("Applied playwright-stealth to page (direct)")

    return pw, browser, context, page


async def _reddit_login_async(page, username: str, password: str, timeout: int = 60) -> bool:
    """Log into Reddit using Playwright.

    Args:
        page: Playwright page instance
        username: Reddit username
        password: Reddit password
        timeout: Max seconds to wait for login

    Returns:
        True if login succeeded, False otherwise
    """
    try:
        # Navigate to login — use 'domcontentloaded' instead of 'networkidle'
        # because Reddit never reaches a fully idle network state (analytics, websockets, etc.)
        print(f"{Colors.CYAN}[*] Navigating to Reddit login...{Colors.RESET}")
        await page.goto('https://www.reddit.com/login/', wait_until='domcontentloaded', timeout=60000)
        await page.wait_for_timeout(3000)

        # Check if we got past the JS challenge
        current_url = page.url
        if 'js_challenge' in current_url:
            print(f"{Colors.YELLOW}[*] JS challenge detected, waiting for resolution...{Colors.RESET}")
            await page.wait_for_timeout(15000)

        # Wait for login form
        print(f"{Colors.CYAN}[*] Looking for login form...{Colors.RESET}")
        username_selectors = [
            "input[name='username']",
            "input#loginUsername",
            "input[type='text']",
        ]

        username_field = None
        for selector in username_selectors:
            try:
                username_field = await page.wait_for_selector(selector, timeout=15000)
                if username_field:
                    logger.info(f"Found username field: {selector}")
                    break
            except Exception:
                continue

        if not username_field:
            logger.error("Could not find username field — Reddit may be blocking the browser")
            return False

        # Fill username
        await username_field.click()
        await username_field.fill(username)
        await page.wait_for_timeout(500)

        # Find and fill password
        password_selectors = [
            "input[name='password']",
            "input#loginPassword",
            "input[type='password']",
        ]

        password_field = None
        for selector in password_selectors:
            try:
                password_field = await page.query_selector(selector)
                if password_field:
                    logger.info(f"Found password field: {selector}")
                    break
            except Exception:
                continue

        if not password_field:
            logger.error("Could not find password field")
            return False

        await password_field.click()
        await password_field.fill(password)
        await page.wait_for_timeout(500)

        # Click login button
        print(f"{Colors.CYAN}[*] Submitting login...{Colors.RESET}")
        login_selectors = [
            "button[type='submit']",
            "button:has-text('Log In')",
        ]

        for selector in login_selectors:
            try:
                btn = await page.query_selector(selector)
                if btn:
                    await btn.click()
                    logger.info(f"Clicked login button: {selector}")
                    break
            except Exception:
                continue
        else:
            await password_field.press('Enter')
            logger.info("Submitted login via Enter key")

        # Wait for login to complete
        await page.wait_for_timeout(5000)

        # Check if login succeeded
        current_url = page.url.lower()
        if 'login' in current_url and 'register' not in current_url:
            try:
                error = await page.query_selector('[data-testid="error-message"], .error')
                if error:
                    error_text = await error.inner_text()
                    logger.error(f"Login error: {error_text}")
                    return False
            except Exception:
                pass
            logger.error("Still on login page — login may have failed")
            return False

        print(f"{Colors.GREEN}[*] Logged in as u/{username}{Colors.RESET}")
        logger.info(f"Reddit login successful for u/{username}")
        return True

    except Exception as e:
        logger.error(f"Reddit login failed: {e}")
        return False


async def _fill_reddit_post_form(page, clean_sub: str, title: str, body: str) -> str:
    """Fill the Reddit submit form on the current page.

    Handles Reddit's current rich text editor which uses contenteditable divs
    instead of standard textarea/input elements.

    Args:
        page: Playwright page already on the submit form
        clean_sub: Subreddit name (no 'r/' prefix)
        title: Post title
        body: Post body text

    Returns:
        Empty string on success, or error message on failure.
    """
    # Wait for the form to fully render after domcontentloaded
    await page.wait_for_timeout(5000)

    # Switch to Text tab (Reddit defaults to "Link" or shows tabs)
    for selector in ["button:has-text('Text')", "button[data-testid='text-tab']"]:
        try:
            tab = await page.query_selector(selector)
            if tab:
                await tab.click()
                await page.wait_for_timeout(1000)
                logger.info(f"Clicked Text tab: {selector}")
                break
        except Exception:
            continue

    # Fill title — Reddit uses a textarea with placeholder "Title*" and "0/100" counter
    title_filled = False
    title_selectors = [
        'textarea[placeholder*="Title"]',
        'textarea[placeholder*="title"]',
        'div[contenteditable="true"][aria-label*="Title"]',
        'input[name="title"]',
        'textarea[name="title"]',
    ]
    for selector in title_selectors:
        try:
            field = await page.wait_for_selector(selector, timeout=10000)
            if field:
                await field.click()
                await field.fill(title)
                title_filled = True
                logger.info(f"Filled title field: {selector}")
                break
        except Exception:
            continue

    if not title_filled:
        return "[ERROR] Could not find title field on Reddit submit page."

    await page.wait_for_timeout(500)

    # Fill body — Reddit uses a contenteditable div (rich text editor).
    # Standard textarea fill() won't work on contenteditable divs,
    # so we use keyboard typing instead.
    body_filled = False

    # Strategy 1: Try contenteditable div with specific attributes
    body_selectors = [
        'div[contenteditable="true"][aria-label*="Body"]',
        'div[contenteditable="true"][data-placeholder*="Body"]',
        'div[contenteditable="true"][aria-label*="text"]',
        'div[contenteditable="true"][data-placeholder*="text"]',
        'div[role="textbox"][contenteditable="true"]',
    ]
    for selector in body_selectors:
        try:
            field = await page.query_selector(selector)
            if field:
                # Check it's not the title field
                aria_label = (await field.get_attribute('aria-label') or '').lower()
                data_placeholder = (await field.get_attribute('data-placeholder') or '').lower()
                if 'title' in aria_label and 'body' not in aria_label:
                    continue
                if 'title' in data_placeholder and 'body' not in data_placeholder:
                    continue

                # Click to focus the contenteditable div
                await field.click()
                await page.wait_for_timeout(300)

                # Type the body text using keyboard (works with contenteditable)
                lines = body.split('\n')
                for i, line in enumerate(lines):
                    await page.keyboard.type(line, delay=5)
                    if i < len(lines) - 1:
                        await page.keyboard.press('Enter')

                body_filled = True
                logger.info(f"Filled body field (contenteditable): {selector}")
                break
        except Exception:
            continue

    # Strategy 2: Generic contenteditable div fallback — find any contenteditable
    # div that is NOT the title field. The title is a textarea, so any
    # contenteditable div on the page should be the body editor.
    if not body_filled:
        try:
            editables = await page.query_selector_all('div[contenteditable="true"]')
            for el in editables:
                # Skip if this is the title (title is usually a textarea, not a div)
                tag = await el.evaluate('e => e.tagName')
                if tag == 'TEXTAREA' or tag == 'INPUT':
                    continue
                # Click to focus
                await el.click()
                await page.wait_for_timeout(300)
                # Type the body text
                lines = body.split('\n')
                for i, line in enumerate(lines):
                    await page.keyboard.type(line, delay=5)
                    if i < len(lines) - 1:
                        await page.keyboard.press('Enter')
                body_filled = True
                logger.info("Filled body field (generic contenteditable fallback)")
                break
        except Exception:
            pass

    # Strategy 3: Fallback to standard textarea (older Reddit UI)
    if not body_filled:
        for selector in ["textarea[placeholder*='Text']", "textarea[placeholder*='text']", "textarea[name='text']"]:
            try:
                field = await page.query_selector(selector)
                if field:
                    placeholder = (await field.get_attribute('placeholder') or '').lower()
                    name = (await field.get_attribute('name') or '').lower()
                    if 'title' in name or 'title' in placeholder:
                        continue
                    await field.click()
                    await field.fill(body)
                    body_filled = True
                    logger.info(f"Filled body field (textarea): {selector}")
                    break
            except Exception:
                continue

    if not body_filled:
        return "[ERROR] Could not find body/text field on Reddit submit page."

    return ""  # Success


async def _open_reddit_post_form_async(subreddit: str, title: str, body: str) -> str:
    """Async: Open Reddit, log in, fill post form, keep browser open for user to click Post."""
    creds = _load_credentials()
    username = creds.get("username", "")
    password = creds.get("password", "")

    if not username or not password:
        return "[ERROR] Missing Reddit username or password. Edit promotion_profile.json → api_credentials.reddit"

    clean_sub = subreddit.replace("r/", "").strip()

    pw, browser, context, page = None, None, None, None
    try:
        # Always use visible browser — Reddit blocks headless
        pw, browser, context, page = await _create_browser(headless=False)

        # Login
        login_ok = await _reddit_login_async(page, username, password)
        if not login_ok:
            return "[ERROR] Reddit login failed. Check your username/password. You may need to handle CAPTCHA in the browser window."

        # Navigate to submit page
        submit_url = f"https://www.reddit.com/r/{clean_sub}/submit"
        print(f"{Colors.CYAN}[*] Navigating to r/{clean_sub} submit page...{Colors.RESET}")
        await page.goto(submit_url, wait_until='domcontentloaded', timeout=30000)
        await page.wait_for_timeout(3000)

        # Fill the form
        error = await _fill_reddit_post_form(page, clean_sub, title, body)
        if error:
            return error

        # Take screenshot
        screenshot_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "scratch", f"reddit_post_form_{clean_sub}.png"
        )
        os.makedirs(os.path.dirname(screenshot_path), exist_ok=True)
        await page.screenshot(path=screenshot_path)

        print(f"\n{Colors.GREEN}═══ REDDIT POST FORM FILLED ═══{Colors.RESET}")
        print(f"  Subreddit: r/{clean_sub}")
        print(f"  Title: {title[:80]}{'...' if len(title) > 80 else ''}")
        print(f"  Body: {body[:80]}{'...' if len(body) > 80 else ''}")
        print(f"  Screenshot: {screenshot_path}")
        print(f"\n{Colors.YELLOW}⚠️  REVIEW the post in the browser window, then click 'Post' yourself.{Colors.RESET}")
        print(f"{Colors.YELLOW}   The browser will stay open for 5 minutes.{Colors.RESET}")
        print(f"{Colors.GRAY}   Close the browser window when done.{Colors.RESET}\n")

        logger.info(f"Reddit post form filled for r/{clean_sub}: {title[:50]}")

        # Keep browser open for 5 minutes
        await page.wait_for_timeout(300000)

        return f"✅ Post form filled for r/{clean_sub}. Browser was kept open for review."

    except Exception as e:
        logger.error(f"Reddit browser posting failed: {e}")
        return f"[ERROR] Reddit browser posting failed: {e}"
    finally:
        try:
            if browser:
                await browser.close()
            if pw:
                await pw.stop()
        except Exception:
            pass


async def _post_to_reddit_browser_async(subreddit: str, title: str, body: str) -> str:
    """Async: Log into Reddit and post automatically."""
    creds = _load_credentials()
    username = creds.get("username", "")
    password = creds.get("password", "")

    if not username or not password:
        return "[ERROR] Missing Reddit username or password. Edit promotion_profile.json → api_credentials.reddit"

    clean_sub = subreddit.replace("r/", "").strip()

    pw, browser, context, page = None, None, None, None
    try:
        # Always use visible browser — Reddit blocks headless
        pw, browser, context, page = await _create_browser(headless=False)

        # Login
        login_ok = await _reddit_login_async(page, username, password)
        if not login_ok:
            return "[ERROR] Reddit login failed. Check your username/password. You may need to handle CAPTCHA in the browser window."

        # Navigate to submit page
        submit_url = f"https://www.reddit.com/r/{clean_sub}/submit"
        print(f"{Colors.CYAN}[*] Navigating to r/{clean_sub} submit page...{Colors.RESET}")
        await page.goto(submit_url, wait_until='domcontentloaded', timeout=30000)
        await page.wait_for_timeout(3000)

        # Fill the form
        error = await _fill_reddit_post_form(page, clean_sub, title, body)
        if error:
            return error

        await page.wait_for_timeout(1000)

        # Click Post button
        print(f"{Colors.CYAN}[*] Submitting post...{Colors.RESET}")
        post_selectors = [
            "button:has-text('Post')",
            "button[data-testid='submit-post']",
            "button[type='submit']",
        ]
        for selector in post_selectors:
            try:
                btn = await page.query_selector(selector)
                if btn:
                    await btn.click()
                    logger.info(f"Clicked post button: {selector}")
                    break
            except Exception:
                continue

        # Wait for redirect
        await page.wait_for_timeout(8000)

        current_url = page.url
        if f"/r/{clean_sub}/comments/" in current_url:
            logger.info(f"Posted to Reddit r/{clean_sub} via browser: {current_url}")
            return f"✅ Posted to r/{clean_sub}\n  Title: {title}\n  URL: {current_url}"
        elif "submit" not in current_url:
            logger.info(f"Posted to Reddit r/{clean_sub} via browser (redirected): {current_url}")
            return f"✅ Posted to r/{clean_sub}\n  Title: {title}\n  URL: {current_url}"
        else:
            try:
                error = await page.query_selector('.error, [data-testid="error"]')
                if error:
                    error_text = await error.inner_text()
                    return f"[ERROR] Reddit post failed: {error_text}"
            except Exception:
                pass
            return f"⚠️ Post submitted to r/{clean_sub} but URL could not be confirmed. Check your Reddit profile."

    except Exception as e:
        logger.error(f"Reddit browser posting failed: {e}")
        return f"[ERROR] Reddit browser posting failed: {e}"
    finally:
        try:
            if browser:
                await browser.close()
            if pw:
                await pw.stop()
        except Exception:
            pass


def check_reddit_browser_ready() -> str:
    """Check if browser-based Reddit posting is ready to use.

    Returns:
        Formatted readiness report.
    """
    output = f"\n{Colors.CYAN}═══ REDDIT BROWSER POSTING READINESS ═══{Colors.RESET}\n"

    # Check Playwright
    try:
        import playwright
        output += f"  Playwright: {Colors.GREEN}INSTALLED{Colors.RESET}\n"
    except ImportError:
        output += f"  Playwright: {Colors.RED}NOT INSTALLED{Colors.RESET} — run: pip install playwright && python -m playwright install chromium\n"
        return output

    # Check playwright-stealth
    try:
        from playwright_stealth import Stealth
        output += f"  playwright-stealth: {Colors.GREEN}INSTALLED{Colors.RESET}\n"
    except ImportError:
        output += f"  playwright-stealth: {Colors.YELLOW}NOT INSTALLED{Colors.RESET} — run: pip install playwright-stealth\n"

    # Check Chromium browser (just check if playwright can find it, don't launch)
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            browser.close()
        output += f"  Chromium: {Colors.GREEN}INSTALLED{Colors.RESET}\n"
    except Exception:
        output += f"  Chromium: {Colors.YELLOW}NOT FOUND{Colors.RESET} — run: python -m playwright install chromium\n"

    # Check credentials
    creds = _load_credentials()
    username = creds.get("username", "")
    password = creds.get("password", "")

    if username and password:
        output += f"  Credentials: {Colors.GREEN}CONFIGURED{Colors.RESET} (u/{username})\n"
    else:
        missing = []
        if not username:
            missing.append("username")
        if not password:
            missing.append("password")
        output += f"  Credentials: {Colors.YELLOW}MISSING{Colors.RESET} ({', '.join(missing)})\n"
        output += f"    → Edit promotion_profile.json → api_credentials.reddit\n"

    # Check PRAW
    praw_ready = False
    try:
        import praw
        if creds.get("client_id") and creds.get("client_secret") and username and password:
            praw_ready = True
    except ImportError:
        pass

    if praw_ready:
        output += f"\n  {Colors.GREEN}PRAW API is also available{Colors.RESET} — browser mode not needed\n"
    else:
        output += f"\n  {Colors.YELLOW}PRAW API not available{Colors.RESET} — browser mode is the fallback\n"

    output += f"\n{Colors.YELLOW}⚠️  Reddit blocks headless browsers — visible browser window required{Colors.RESET}"
    output += f"\n{Colors.GRAY}Uses Playwright + stealth for bot detection evasion{Colors.RESET}"
    output += f"\n{Colors.GRAY}Use open_reddit_post_form() for semi-auto (you click Post){Colors.RESET}"
    output += f"\n{Colors.GRAY}Use post_to_reddit_browser() for fully automatic posting{Colors.RESET}"

    return output


@hitl_risk(RiskLevel.HIGH)
def open_reddit_post_form(subreddit: str, title: str, body: str,
                          headless: bool = False) -> str:
    """Open Reddit in a visible browser, log in, and fill in a post form.

    This is the RECOMMENDED way to post to Reddit when PRAW API access is not
    available. It opens a real browser window, logs in, fills the form, and
    keeps the browser open for you to review and click "Post" yourself.

    HIGH RISK: Opens a browser and fills in credentials, but does NOT post
    automatically. You must click "Post" in the browser window.

    IMPORTANT: A visible browser window will open (Reddit blocks headless).

    Args:
        subreddit: Target subreddit name (without 'r/', e.g. 'Python')
        title: Post title
        body: Post body text (Markdown supported)
        headless: Ignored — always uses visible browser for Reddit

    Returns:
        Status message. Browser window stays open for you to click Post.
    """
    return _run_async(_open_reddit_post_form_async(subreddit, title, body))


@hitl_risk(RiskLevel.CRITICAL)
def post_to_reddit_browser(subreddit: str, title: str, body: str,
                           headless: bool = False) -> str:
    """Post a text submission to Reddit using Playwright browser automation.

    CRITICAL RISK: This performs an irreversible public action. Requires explicit
    operator approval (must type YES) before executing. The browser will log in,
    fill the form, and click Post automatically.

    Uses Playwright with playwright-stealth. A visible browser window will open
    because Reddit blocks headless browsers.

    Args:
        subreddit: Target subreddit name (without 'r/', e.g. 'Python')
        title: Post title
        body: Post body text (Markdown supported)
        headless: Ignored — always uses visible browser for Reddit

    Returns:
        Confirmation message with post URL, or error message.
    """
    return _run_async(_post_to_reddit_browser_async(subreddit, title, body))