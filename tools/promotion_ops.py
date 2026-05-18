"""
Promotion tools — Agent-callable functions for generating platform-specific promotional content.

These tools allow the autonomous agent to render, preview, and save promotional
content for any supported platform (Reddit, Hacker News, Discord, X/Twitter).
The agent can also customize the profile fields before rendering.

Includes git scan workflow: scan a GitHub user's repos, rotate through them,
and generate fresh promotional content for a different repo each cycle.

HITL Risk Classification:
- render_promotion_post: LOW (read-only, generates text)
- preview_all_platforms: LOW (read-only, displays text)
- save_promotion_content: MEDIUM (writes files to disk)
- update_promotion_profile: MEDIUM (modifies promotion_profile.json)
- scan_and_promote: LOW (read-only scan + render, no posting)
"""

import json
import os
from typing import Optional

from core.config import Colors, logger, get_current_session_reports_dir
from core.promotion import PromotionEngine


# Module-level engine instance (lazy-loaded)
_engine: Optional[PromotionEngine] = None


def _get_engine() -> PromotionEngine:
    """Get or create the singleton PromotionEngine instance."""
    global _engine
    if _engine is None:
        _engine = PromotionEngine()
    return _engine


def render_promotion_post(platform: str) -> str:
    """Render promotional content for a specific platform.

    Generates platform-tailored promotional content following each platform's
    cultural norms and posting rules. Supported platforms: reddit, hacker_news,
    discord, twitter.

    Args:
        platform: Target platform name. One of: 'reddit', 'hacker_news', 'discord', 'twitter'

    Returns:
        Formatted promotional content string for the specified platform.
    """
    engine = _get_engine()

    try:
        result = engine.render(platform)
    except ValueError as e:
        return f"[ERROR] {e}"

    platform_display = platform.replace("_", " ").title()

    # Format output based on platform type
    if platform == "reddit":
        output = f"{Colors.CYAN}═══ REDDIT POST ═══{Colors.RESET}\n"
        output += f"{Colors.YELLOW}Title:{Colors.RESET} {result.get('title', '')}\n\n"
        output += f"{Colors.YELLOW}Body:{Colors.RESET}\n{result.get('body', '')}\n"
        if result.get("subreddits"):
            output += f"\n{Colors.GRAY}Target Subreddits: {', '.join(result['subreddits'])}{Colors.RESET}"
        if result.get("posting_rules"):
            output += f"\n{Colors.GRAY}⚠️ Posting Rules: {result['posting_rules']}{Colors.RESET}"

    elif platform == "hacker_news":
        output = f"{Colors.CYAN}═══ HACKER NEWS (Show HN) ═══{Colors.RESET}\n"
        output += f"{Colors.YELLOW}Title:{Colors.RESET} {result.get('title', '')}\n\n"
        output += f"{Colors.YELLOW}Required First Comment:{Colors.RESET}\n{result.get('first_comment', '')}\n"
        if result.get("posting_rules"):
            output += f"\n{Colors.GRAY}⚠️ Posting Rules: {result['posting_rules']}{Colors.RESET}"

    elif platform == "discord":
        output = f"{Colors.CYAN}═══ DISCORD MESSAGE ═══{Colors.RESET}\n"
        output += f"{result.get('message', '')}\n"
        if result.get("servers"):
            output += f"\n{Colors.GRAY}Target Servers: {', '.join(result['servers'])}{Colors.RESET}"
        if result.get("posting_rules"):
            output += f"\n{Colors.GRAY}⚠️ Posting Rules: {result['posting_rules']}{Colors.RESET}"

    elif platform == "twitter":
        output = f"{Colors.CYAN}═══ X / TWITTER TWEET ═══{Colors.RESET}\n"
        output += f"{result.get('tweet', '')}\n"
        if result.get("media_note"):
            output += f"\n{Colors.YELLOW}{result['media_note']}{Colors.RESET}"
        if result.get("character_count"):
            output += f"\n{Colors.GRAY}Character count: {result['character_count']}{Colors.RESET}"
        if result.get("posting_rules"):
            output += f"\n{Colors.GRAY}⚠️ Posting Rules: {result['posting_rules']}{Colors.RESET}"

    else:
        output = json.dumps(result, indent=2, default=str)

    logger.info(f"Rendered promotion post for {platform_display}")
    return output


def preview_all_platforms() -> str:
    """Preview promotional content for all enabled platforms at once.

    Renders content for Reddit, Hacker News, Discord, and X/Twitter in a
    single formatted output. Disabled platforms are skipped.

    Returns:
        Formatted preview of all platform promotional content.
    """
    engine = _get_engine()

    # Show profile summary first
    summary = engine.get_platform_summary()
    output = summary + "\n\n"

    # Render each platform
    all_content = engine.render_all()

    if not all_content:
        return output + f"{Colors.YELLOW}[!] No platforms enabled in promotion_profile.json{Colors.RESET}"

    for platform, content in all_content.items():
        platform_display = platform.replace("_", " ").title()
        output += f"\n{Colors.CYAN}{'─' * 50}{Colors.RESET}\n"
        output += f"{Colors.GREEN}▶ {platform_display}{Colors.RESET}\n"
        output += f"{Colors.CYAN}{'─' * 50}{Colors.RESET}\n"

        if platform == "reddit":
            output += f"{Colors.YELLOW}Title:{Colors.RESET} {content.get('title', '')}\n\n"
            output += f"{content.get('body', '')}\n"
        elif platform == "hacker_news":
            output += f"{Colors.YELLOW}Title:{Colors.RESET} {content.get('title', '')}\n\n"
            output += f"{Colors.YELLOW}First Comment:{Colors.RESET}\n{content.get('first_comment', '')}\n"
        elif platform == "discord":
            output += f"{content.get('message', '')}\n"
        elif platform == "twitter":
            output += f"{content.get('tweet', '')}\n"
            if content.get("media_note"):
                output += f"\n{Colors.YELLOW}{content['media_note']}{Colors.RESET}"

        output += "\n"

    logger.info("Previewed all platform promotion content")
    return output


def save_promotion_content(platform: str = "all") -> str:
    """Save rendered promotional content to markdown files in the session reports directory.

    Generates platform-specific markdown files for each enabled platform.
    Use 'all' to save content for every enabled platform.

    Args:
        platform: Platform name ('reddit', 'hacker_news', 'discord', 'twitter')
                  or 'all' to save all platforms. Defaults to 'all'.

    Returns:
        Confirmation message with file paths.
    """
    engine = _get_engine()

    if platform.lower() == "all":
        output_files = engine.render_all_to_files()
    else:
        try:
            content = engine.render(platform)
        except ValueError as e:
            return f"[ERROR] {e}"

        report_dir = get_current_session_reports_dir()
        from datetime import datetime
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"promotion_{platform}_{timestamp}.md"
        filepath = os.path.join(report_dir, filename)

        md_content = engine._format_as_markdown(platform, content)

        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(md_content)
            output_files = {platform: filepath}
        except Exception as e:
            return f"[ERROR] Failed to save: {e}"

    result_lines = [f"{Colors.GREEN}✅ Promotion content saved:{Colors.RESET}"]
    for plat, path in output_files.items():
        result_lines.append(f"  {plat.replace('_', ' ').title()}: {path}")

    logger.info(f"Saved promotion content for {platform}: {output_files}")
    return "\n".join(result_lines)


def update_promotion_profile(field: str, value: str) -> str:
    """Update a field in the promotion profile and re-render content.

    Allows the agent to customize the promotion profile before generating
    content. Uses dot-notation for nested fields (e.g., 'identity.name',
    'project.repo_url', 'project.name').

    Args:
        field: Dot-notation path to the field to update (e.g., 'project.repo_url')
        value: New value for the field

    Returns:
        Confirmation message showing the updated field and value.
    """
    global _engine

    profile_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "promotion_profile.json"
    )

    try:
        with open(profile_path, 'r', encoding='utf-8') as f:
            profile = json.load(f)
    except Exception as e:
        return f"[ERROR] Failed to load promotion_profile.json: {e}"

    # Navigate to the nested key and update
    keys = field.split(".")
    obj = profile
    for key in keys[:-1]:
        if key not in obj or not isinstance(obj[key], dict):
            return f"[ERROR] Invalid field path: '{field}'. '{key}' is not a nested object."
        obj = obj[key]

    final_key = keys[-1]
    if final_key not in obj:
        return f"[ERROR] Field '{field}' does not exist in promotion_profile.json"

    # Try to parse value as JSON (for lists, bools, numbers)
    parsed_value = value
    try:
        parsed_value = json.loads(value)
    except (json.JSONDecodeError, ValueError):
        pass  # Keep as string

    old_value = obj[final_key]
    obj[final_key] = parsed_value

    # Save updated profile
    try:
        with open(profile_path, 'w', encoding='utf-8') as f:
            json.dump(profile, f, indent=2, ensure_ascii=False)
    except Exception as e:
        return f"[ERROR] Failed to save promotion_profile.json: {e}"

    # Reset engine so it reloads on next use
    _engine = None

    logger.info(f"Updated promotion profile: {field} = {parsed_value} (was: {old_value})")
    return f"{Colors.GREEN}✅ Updated promotion_profile.json:{Colors.RESET}\n  {field}: {old_value} → {parsed_value}\n\n{Colors.GRAY}The promotion engine will use the new value on next render.{Colors.RESET}"


def scan_and_promote(username: str = "", platform: str = "all") -> str:
    """Scan GitHub repos, inject the next repo into the promotion engine, and render content.

    This is the one-shot workflow for promoting a different repo each cycle:
    1. Scans the GitHub user's public repos (or uses cached rotation)
    2. Selects the next repo to promote (respecting cooldown)
    3. Injects the repo data into the promotion engine
    4. Renders promotional content for the specified platform(s)

    Args:
        username: GitHub username to scan. Defaults to the value in
                  promotion_profile.json → git_scan.username.
        platform: Platform to render for ('reddit', 'hacker_news', 'discord',
                  'twitter', or 'all'). Defaults to 'all'.

    Returns:
        Formatted promotional content for the selected repo and platform(s).
    """
    from tools.git_scan_ops import scan_git_repos, get_next_repo_to_promote, inject_repo_to_promote

    # Step 1: Scan repos (refreshes cache from GitHub API)
    scan_result = scan_git_repos(username)
    print(scan_result)
    print()

    # Step 2: Get next repo to promote
    next_result = get_next_repo_to_promote()
    print(next_result)
    print()

    # Step 3: Inject into promotion engine
    inject_result = inject_repo_to_promote()
    print(inject_result)
    print()

    # Step 4: Render content
    engine = _get_engine()
    if platform.lower() == "all":
        all_content = engine.render_all()
        if not all_content:
            return f"{Colors.YELLOW}[!] No platforms enabled in promotion_profile.json{Colors.RESET}"

        output = f"\n{Colors.CYAN}{'═' * 55}{Colors.RESET}\n"
        output += f"{Colors.GREEN}📣 Promotional Content — {engine._get('project.name', 'Unknown')}{Colors.RESET}\n"
        output += f"{Colors.CYAN}{'═' * 55}{Colors.RESET}\n"

        for plat, content in all_content.items():
            plat_display = plat.replace("_", " ").title()
            output += f"\n{Colors.CYAN}{'─' * 50}{Colors.RESET}\n"
            output += f"{Colors.GREEN}▶ {plat_display}{Colors.RESET}\n"
            output += f"{Colors.CYAN}{'─' * 50}{Colors.RESET}\n"

            if plat == "reddit":
                output += f"{Colors.YELLOW}Title:{Colors.RESET} {content.get('title', '')}\n\n"
                output += f"{content.get('body', '')}\n"
            elif plat == "hacker_news":
                output += f"{Colors.YELLOW}Title:{Colors.RESET} {content.get('title', '')}\n\n"
                output += f"{Colors.YELLOW}First Comment:{Colors.RESET}\n{content.get('first_comment', '')}\n"
            elif plat == "discord":
                output += f"{content.get('message', '')}\n"
            elif plat == "twitter":
                output += f"{content.get('tweet', '')}\n"
                if content.get("media_note"):
                    output += f"\n{Colors.YELLOW}{content['media_note']}{Colors.RESET}"
            output += "\n"

        return output
    else:
        try:
            content = engine.render(platform)
        except ValueError as e:
            return f"{Colors.RED}[ERROR] {e}{Colors.RESET}"

        plat_display = platform.replace("_", " ").title()
        output = f"\n{Colors.CYAN}{'═' * 55}{Colors.RESET}\n"
        output += f"{Colors.GREEN}📣 {plat_display} — {engine._get('project.name', 'Unknown')}{Colors.RESET}\n"
        output += f"{Colors.CYAN}{'═' * 55}{Colors.RESET}\n"

        if platform == "reddit":
            output += f"{Colors.YELLOW}Title:{Colors.RESET} {content.get('title', '')}\n\n"
            output += f"{content.get('body', '')}\n"
        elif platform == "hacker_news":
            output += f"{Colors.YELLOW}Title:{Colors.RESET} {content.get('title', '')}\n\n"
            output += f"{Colors.YELLOW}First Comment:{Colors.RESET}\n{content.get('first_comment', '')}\n"
        elif platform == "discord":
            output += f"{content.get('message', '')}\n"
        elif platform == "twitter":
            output += f"{content.get('tweet', '')}\n"
            if content.get("media_note"):
                output += f"\n{Colors.YELLOW}{content['media_note']}{Colors.RESET}"

        return output


def get_promotion_tips(platform: str) -> str:
    """Get platform-specific posting tips and rules for effective promotion.

    Returns curated advice on how to successfully share your project on each
    platform, including what to avoid and what works well.

    Args:
        platform: Platform name. One of: 'reddit', 'hacker_news', 'discord', 'twitter'

    Returns:
        Formatted tips and rules for the specified platform.
    """
    tips = {
        "reddit": """Reddit Posting Tips (r/netsec, r/HowToHack, r/Python):
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✅ DO:
  • Focus entirely on technical architecture & problem solved
  • Post in the monthly "discussion & tool thread" for new tools (r/netsec)
  • Include source code links and explain how it works under the hood
  • Ask for feedback — Redditors love giving technical feedback
  • Be transparent about limitations and active development status

❌ DON'T:
  • Use marketing language ("amazing", "revolutionary", "game-changing")
  • Post standalone tool announcements in r/netsec without meeting their bar
  • Omit the technical details of how it's built
  • Self-promote without contributing to the discussion
  • Ignore the subreddit-specific rules (each sub is different)""",

        "hacker_news": """Hacker News (Show HN) Posting Tips:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✅ DO:
  • Use "Show HN:" prefix — it's strictly for things people can use right now
  • Keep titles factual with NO hype words ("amazing", "simple", "easy")
  • Post the first comment yourself explaining technical trade-offs
  • Share your background and the specific itch you were scratching
  • Be responsive to technical questions in the comments
  • Disclose limitations honestly

❌ DON'T:
  • Use clickbait or marketing-style titles
  • Ask for upvotes or share on social media for votes (against HN rules)
  • Post if the project isn't usable right now (no "coming soon")
  • Ignore the first-comment requirement
  • Get defensive about criticism — HN values honest technical discussion""",

        "discord": """Discord (Security/CTF Servers) Posting Tips:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✅ DO:
  • Keep it conversational and brief
  • Immediately state what's in it for them (time-saving, automation)
  • Use code blocks for formatting technical details
  • Tag good-first-issue bugs if you want contributors
  • Be available to answer questions and take feature requests
  • Post in the appropriate channel (tools, projects, showcase)

❌ DON'T:
  • Write a wall of text — Discord users scroll past long messages
  • Use formal/marketing tone — be casual and genuine
  • Spam multiple channels with the same message
  • Post without context about why it's useful to THIS community
  • Forget to mention it's open-source""",

        "twitter": """X / Twitter Posting Tips:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✅ DO:
  • Attach visuals — 1280x640 social preview image or 15s terminal GIF
  • Use relevant hashtags (#infosec #python #cybersecurity #redteaming)
  • Keep it punchy — lead with the hook, link at the end
  • Thread follow-up tweets with technical details
  • Tag relevant accounts sparingly
  • Post during peak hours (9-11am / 7-9pm in target timezone)

❌ DON'T:
  • Post without an image or GIF — text-only tweets get 50% less engagement
  • Use too many hashtags (3-5 max)
  • Write a paragraph — break into short, scannable lines
  • Forget the GitHub link
  • Post and ghost — engage with replies and quote-tweets"""
    }

    platform_key = platform.lower().replace(" ", "_")
    if platform_key not in tips:
        valid = ", ".join(tips.keys())
        return f"[ERROR] Unknown platform '{platform}'. Valid platforms: {valid}"

    logger.info(f"Retrieved promotion tips for {platform}")
    return tips[platform_key]