"""
Posting tools — Agent-callable functions for posting promotional content to platforms.

These tools use platform APIs to actually post content:
  - Reddit: PRAW (Python Reddit API Wrapper) — posts link/text submissions
    Fallback: Selenium browser automation when API access is not available
  - Twitter/X: Tweepy — posts tweets with optional media
  - Discord: Webhook URLs — sends messages to channel webhooks
  - Hacker News: NO API — must be posted manually

HITL Risk Classification:
ALL posting tools are CRITICAL because they perform irreversible public actions.
Every post requires explicit "YES" confirmation from the operator before executing.
"""

import json
import os
from typing import Any, Optional

from core.config import Colors, logger
from core.hitl import hitl_risk, RiskLevel
from core.promotion import PromotionEngine


def _load_credentials() -> dict:
    """Load API credentials from promotion_profile.json."""
    profile_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "promotion_profile.json"
    )
    try:
        with open(profile_path, 'r', encoding='utf-8') as f:
            profile = json.load(f)
        return profile.get("api_credentials", {})
    except Exception as e:
        logger.error(f"Failed to load credentials: {e}")
        return {}


def _check_credentials(platform: str) -> tuple[bool, str]:
    """Check if API credentials are configured for a platform.

    Returns:
        Tuple of (is_configured, missing_fields_message)
    """
    creds = _load_credentials()

    if platform == "reddit":
        reddit_creds = creds.get("reddit", {})
        required = ["client_id", "client_secret", "username", "password"]
        missing = [f for f in required if not reddit_creds.get(f)]
        if missing:
            return False, f"Missing Reddit API credentials: {', '.join(missing)}. Edit promotion_profile.json → api_credentials.reddit OR use browser fallback (see reddit_browser_ops.py)"
        return True, ""

    elif platform == "reddit_browser":
        reddit_creds = creds.get("reddit", {})
        required = ["username", "password"]
        missing = [f for f in required if not reddit_creds.get(f)]
        if missing:
            return False, f"Missing Reddit browser credentials: {', '.join(missing)}. Edit promotion_profile.json → api_credentials.reddit (only username and password needed for browser mode)"
        return True, ""

    elif platform == "twitter":
        twitter_creds = creds.get("twitter", {})
        # Tweepy v2 needs bearer_token OR (api_key + api_secret + access_token + access_token_secret)
        has_v2 = bool(twitter_creds.get("bearer_token"))
        has_v1 = all([
            twitter_creds.get("api_key"),
            twitter_creds.get("api_secret"),
            twitter_creds.get("access_token"),
            twitter_creds.get("access_token_secret")
        ])
        if not has_v2 and not has_v1:
            return False, "Missing Twitter credentials. Need either bearer_token OR (api_key + api_secret + access_token + access_token_secret). Edit promotion_profile.json → api_credentials.twitter"
        return True, ""

    elif platform == "discord":
        discord_creds = creds.get("discord", {})
        webhooks = discord_creds.get("webhook_urls", [])
        if not webhooks or not any(webhooks):
            return False, "Missing Discord webhook URLs. Edit promotion_profile.json → api_credentials.discord.webhook_urls"
        return True, ""

    return False, f"Unknown platform: {platform}"


@hitl_risk(RiskLevel.CRITICAL)
def post_to_reddit(subreddit: str, title: str, body: str, is_link_post: bool = False,
                   use_browser: bool = False) -> str:
    """Post a submission to a Reddit subreddit using PRAW or browser fallback.

    CRITICAL RISK: This performs an irreversible public action. Requires explicit
    operator approval (must type YES) before executing.

    By default, uses PRAW (Reddit API). If PRAW credentials are not configured
    or use_browser is True, falls back to Selenium browser automation which
    only requires username and password (no API app needed).

    Args:
        subreddit: Target subreddit name (without the 'r/', e.g. 'netsec')
        title: Post title
        body: Post body text (for text posts) or URL (for link posts)
        is_link_post: If True, body is treated as a URL for a link post
        use_browser: If True, force browser-based posting instead of PRAW

    Returns:
        Confirmation message with post URL, or error message.
    """
    # Clean subreddit name (strip r/ prefix if present)
    clean_sub = subreddit.replace("r/", "").strip()

    # Check if we should use browser mode
    praw_configured, _ = _check_credentials("reddit")
    browser_configured, browser_missing = _check_credentials("reddit_browser")

    if use_browser or not praw_configured:
        # Fall back to browser-based posting
        if not browser_configured:
            return f"[ERROR] {browser_missing}"

        logger.info(f"Using browser fallback for Reddit post to r/{clean_sub}")
        from tools.reddit_browser_ops import post_to_reddit_browser
        return post_to_reddit_browser(
            subreddit=clean_sub,
            title=title,
            body=body
        )

    # Use PRAW (API) mode
    creds = _load_credentials().get("reddit", {})

    try:
        import praw
    except ImportError:
        return "[ERROR] PRAW not installed. Run: pip install praw"

    try:
        reddit = praw.Reddit(
            client_id=creds["client_id"],
            client_secret=creds["client_secret"],
            username=creds["username"],
            password=creds["password"],
            user_agent=creds.get("user_agent", f"PromotionAgent/1.0 by {creds['username']}")
        )

        # Verify authentication
        reddit.user.me()

        if is_link_post:
            submission = reddit.subreddit(clean_sub).submit(title=title, url=body)
        else:
            submission = reddit.subreddit(clean_sub).submit(title=title, selftext=body)

        post_url = f"https://www.reddit.com{submission.permalink}"
        logger.info(f"Posted to Reddit r/{clean_sub}: {post_url}")
        return f"✅ Posted to r/{clean_sub}\n  Title: {title}\n  URL: {post_url}\n  Score: {submission.score}"

    except Exception as e:
        logger.error(f"Reddit post failed: {e}")
        return f"[ERROR] Reddit post failed: {e}"


@hitl_risk(RiskLevel.CRITICAL)
def post_to_twitter(tweet_text: str, media_path: str = "", thread: Optional[list[str]] = None) -> str:
    """Post a tweet or thread to X/Twitter using Tweepy.

    CRITICAL RISK: This performs an irreversible public action. Requires explicit
    operator approval (must type YES) before executing.

    Args:
        tweet_text: Single tweet text content (max 280 characters).
                    Used if thread is None.
        media_path: Optional path to an image/GIF file to attach (first tweet only).
        thread: Optional list of tweet texts to post as a thread. If provided,
                tweet_text is ignored and each item in the list is posted as
                a reply to the previous tweet.

    Returns:
        Confirmation message with tweet URL(s), or error message.
    """
    # Check credentials
    configured, missing_msg = _check_credentials("twitter")
    if not configured:
        return f"[ERROR] {missing_msg}"

    creds = _load_credentials().get("twitter", {})

    try:
        import tweepy
    except ImportError:
        return "[ERROR] Tweepy not installed. Run: pip install tweepy"

    # Determine tweets to post
    tweets_to_post = thread if thread else [tweet_text]

    # Validate all tweet lengths
    for i, t in enumerate(tweets_to_post):
        if len(t) > 280:
            return f"[ERROR] Tweet {i+1} exceeds 280 characters ({len(t)} chars). Shorten the text."

    try:
        # OAuth 1.0a User Context is required for posting tweets
        # Bearer token alone only allows read-only app-level access
        if not all([creds.get("api_key"), creds.get("api_secret"),
                     creds.get("access_token"), creds.get("access_token_secret")]):
            return "[ERROR] Twitter posting requires OAuth 1.0a User Context credentials (api_key, api_secret, access_token, access_token_secret). Edit promotion_profile.json → api_credentials.twitter"

        # Create OAuth1 handler for v1.1 API (needed for media upload)
        auth = tweepy.OAuth1UserHandler(
            creds["api_key"],
            creds["api_secret"],
            creds["access_token"],
            creds["access_token_secret"]
        )
        api_v1 = tweepy.API(auth)

        # Create v2 Client with OAuth 1.0a user context for posting
        client = tweepy.Client(
            consumer_key=creds["api_key"],
            consumer_secret=creds["api_secret"],
            access_token=creds["access_token"],
            access_token_secret=creds["access_token_secret"]
        )

        # Post tweet(s) using v2 API
        tweet_urls: list[str] = []
        previous_tweet_id: Optional[str] = None

        for i, tweet_content in enumerate(tweets_to_post):
            media_ids_arg: Optional[list] = None
            reply_to: Optional[str] = previous_tweet_id

            # Attach media to first tweet only
            if i == 0 and media_path and os.path.exists(media_path):
                media = api_v1.media_upload(media_path)
                media_ids_arg = [media.media_id]

            if media_ids_arg and reply_to:
                response = client.create_tweet(
                    text=tweet_content,
                    media_ids=media_ids_arg,
                    in_reply_to_tweet_id=reply_to
                )
            elif media_ids_arg:
                response = client.create_tweet(
                    text=tweet_content,
                    media_ids=media_ids_arg
                )
            elif reply_to:
                response = client.create_tweet(
                    text=tweet_content,
                    in_reply_to_tweet_id=reply_to
                )
            else:
                response = client.create_tweet(text=tweet_content)

            current_id = response.data["id"]  # type: ignore[index]
            current_url = f"https://twitter.com/i/status/{current_id}"
            tweet_urls.append(current_url)
            previous_tweet_id = current_id

            logger.info(f"Posted tweet {i+1}/{len(tweets_to_post)}: {current_url}")

        if len(tweets_to_post) == 1:
            return f"✅ Tweet posted\n  URL: {tweet_urls[0]}\n  Characters: {len(tweets_to_post[0])}"
        else:
            urls_str = "\n  ".join(f"Tweet {i+1}: {url}" for i, url in enumerate(tweet_urls))
            return f"✅ Thread posted ({len(tweets_to_post)} tweets)\n  {urls_str}"

    except Exception as e:
        logger.error(f"Twitter post failed: {e}")
        return f"[ERROR] Twitter post failed: {e}"


@hitl_risk(RiskLevel.CRITICAL)
def post_to_discord(message: str, webhook_url: str = "") -> str:
    """Post a message to Discord using a webhook.

    CRITICAL RISK: This performs an irreversible public action. Requires explicit
    operator approval (must type YES) before executing.

    Args:
        message: Message text to post (supports Markdown formatting)
        webhook_url: Optional specific webhook URL. If not provided, uses
                     the first webhook from promotion_profile.json

    Returns:
        Confirmation message, or error message.
    """
    # Determine webhook URL
    if not webhook_url:
        creds = _load_credentials().get("discord", {})
        webhooks = creds.get("webhook_urls", [])
        if webhooks:
            webhook_url = webhooks[0]
        else:
            return "[ERROR] No Discord webhook URL provided and none configured in promotion_profile.json"

    try:
        import requests
    except ImportError:
        return "[ERROR] Requests not installed. Run: pip install requests"

    try:
        payload = {
            "content": message,
            "username": "Promotion Agent"
        }

        response = requests.post(
            webhook_url,
            json=payload,
            timeout=30
        )

        if response.status_code in (200, 204):
            logger.info(f"Posted to Discord webhook: {webhook_url[:50]}...")
            return f"✅ Posted to Discord\n  Webhook: {webhook_url[:60]}...\n  Status: {response.status_code}"
        else:
            error_msg = f"HTTP {response.status_code}: {response.text[:200]}"
            logger.error(f"Discord post failed: {error_msg}")
            return f"[ERROR] Discord post failed: {error_msg}"

    except Exception as e:
        logger.error(f"Discord post failed: {e}")
        return f"[ERROR] Discord post failed: {e}"


@hitl_risk(RiskLevel.CRITICAL)
def post_to_all_platforms(dry_run: bool = True) -> str:
    """Post promotional content to all enabled and configured platforms.

    CRITICAL RISK: This performs irreversible public actions across multiple
    platforms. Requires explicit operator approval (must type YES) before
    each platform post executes.

    If dry_run is True (default), content is rendered and displayed but NOT posted.
    Set dry_run to False to actually post.

    Args:
        dry_run: If True, only preview what would be posted. If False, actually post.

    Returns:
        Summary of posting results for each platform.
    """
    engine = PromotionEngine()
    all_content = engine.render_all()
    results = []

    if dry_run:
        results.append(f"{Colors.YELLOW}═══ DRY RUN — No content will be posted ═══{Colors.RESET}\n")

    # ─── Reddit ────────────────────────────────────────────────────────
    if "reddit" in all_content:
        reddit = all_content["reddit"]
        praw_configured, praw_missing = _check_credentials("reddit")
        browser_configured, browser_missing = _check_credentials("reddit_browser")

        if not praw_configured and not browser_configured:
            results.append(f"Reddit: ⏭️ SKIPPED — No API or browser credentials")
            results.append(f"  API: {praw_missing}")
            results.append(f"  Browser: {browser_missing}")
        elif dry_run:
            mode = "PRAW API" if praw_configured else "Browser fallback"
            results.append(f"Reddit: 📋 READY ({mode}) — r/{', '.join(s.replace('r/', '') for s in reddit.get('subreddits', []))}")
            results.append(f"  Title: {reddit.get('title', '')}")
            results.append(f"  Body preview: {reddit.get('body', '')[:100]}...")
        else:
            # Post to each subreddit
            for sub in reddit.get("subreddits", []):
                clean_sub = sub.replace("r/", "")
                result = post_to_reddit(
                    subreddit=clean_sub,
                    title=reddit.get("title", ""),
                    body=reddit.get("body", ""),
                    use_browser=not praw_configured
                )
                results.append(f"Reddit r/{clean_sub}: {result}")

    # ─── Hacker News ──────────────────────────────────────────────────
    if "hacker_news" in all_content:
        results.append("Hacker News: ⏭️ MANUAL — No posting API exists. Post at https://news.ycombinator.com/submit")
        hn = all_content["hacker_news"]
        if dry_run:
            results.append(f"  Title: {hn.get('title', '')}")
            results.append(f"  First comment preview: {hn.get('first_comment', '')[:100]}...")

    # ─── Discord ──────────────────────────────────────────────────────
    if "discord" in all_content:
        configured, missing = _check_credentials("discord")

        if not configured:
            results.append(f"Discord: ⏭️ SKIPPED — {missing}")
        elif dry_run:
            results.append("Discord: 📋 READY — webhook configured")
            results.append(f"  Message preview: {all_content['discord'].get('message', '')[:100]}...")
        else:
            result = post_to_discord(message=all_content["discord"].get("message", ""))
            results.append(f"Discord: {result}")

    # ─── Twitter ──────────────────────────────────────────────────────
    if "twitter" in all_content:
        configured, missing = _check_credentials("twitter")
        is_thread = all_content["twitter"].get("is_thread", False)

        if not configured:
            results.append(f"Twitter: ⏭️ SKIPPED — {missing}")
        elif dry_run:
            tweet = all_content["twitter"].get("tweet", "")
            if is_thread:
                thread = all_content["twitter"].get("thread", [])
                results.append(f"Twitter: 📋 READY — thread ({len(thread)} tweets)")
                for idx, t in enumerate(thread):
                    preview = t[:80] + "..." if len(t) > 80 else t
                    results.append(f"  Tweet {idx+1}/{len(thread)} ({len(t)} chars): {preview}")
            else:
                results.append(f"Twitter: 📋 READY — {len(tweet)} chars")
                results.append(f"  Tweet preview: {tweet[:100]}...")
            if all_content["twitter"].get("media_note"):
                results.append(f"  ⚠️  {all_content['twitter']['media_note']}")
        else:
            if is_thread:
                thread = all_content["twitter"].get("thread", [])
                result = post_to_twitter(tweet_text="", thread=thread)
            else:
                result = post_to_twitter(tweet_text=all_content["twitter"].get("tweet", ""))
            results.append(f"Twitter: {result}")

    return "\n".join(results)


def check_posting_readiness() -> str:
    """Check which platforms have API credentials configured and are ready to post.

    Returns a formatted status report showing which platforms can post automatically,
    which need credentials, and which require manual posting. For Reddit, also
    checks browser fallback readiness when PRAW API is not configured.

    Returns:
        Formatted readiness report for all platforms.
    """
    engine = PromotionEngine()
    all_content = engine.render_all()

    output = f"\n{Colors.CYAN}═══ POSTING READINESS CHECK ═══{Colors.RESET}\n\n"

    platforms = {
        "reddit": {"name": "Reddit", "has_api": True, "has_browser_fallback": True},
        "hacker_news": {"name": "Hacker News", "has_api": False, "has_browser_fallback": False},
        "discord": {"name": "Discord", "has_api": True, "has_browser_fallback": False},
        "twitter": {"name": "Twitter/X", "has_api": True, "has_browser_fallback": False},
    }

    for key, info in platforms.items():
        display = info["name"]
        has_api = info["has_api"]
        has_browser = info.get("has_browser_fallback", False)
        enabled = key in all_content

        if not enabled:
            output += f"  {display}: {Colors.RED}DISABLED{Colors.RESET} (not enabled in profile)\n"
            continue

        if not has_api:
            output += f"  {display}: {Colors.YELLOW}MANUAL ONLY{Colors.RESET} — no posting API exists\n"
            output += f"    → Post at https://news.ycombinator.com/submit\n"
            continue

        configured, missing = _check_credentials(key)

        if configured:
            output += f"  {display}: {Colors.GREEN}READY TO POST{Colors.RESET} ✅ (API)\n"
        elif has_browser:
            # Check browser fallback
            browser_ok, browser_missing = _check_credentials(f"{key}_browser")
            if browser_ok:
                output += f"  {display}: {Colors.YELLOW}READY (Browser){Colors.RESET} 🌐\n"
                output += f"    → API not configured, but browser fallback is available\n"
                output += f"    → Set Reddit API credentials for full API mode\n"
            else:
                output += f"  {display}: {Colors.YELLOW}NEEDS CREDENTIALS{Colors.RESET} ⚠️\n"
                output += f"    → API: {missing}\n"
                output += f"    → Browser: {browser_missing}\n"
        else:
            output += f"  {display}: {Colors.YELLOW}NEEDS CREDENTIALS{Colors.RESET} ⚠️\n"
            output += f"    → {missing}\n"

    output += f"\n{Colors.GRAY}To add credentials: Edit promotion_profile.json → api_credentials{Colors.RESET}"
    output += f"\n{Colors.GRAY}Reddit browser fallback: Only username & password needed (no API app){Colors.RESET}"
    output += f"\n{Colors.GRAY}To post: python promote.py --post (dry run first, then --post --confirm){Colors.RESET}"

    return output