"""
Promotion Engine — Platform-specific template rendering for project promotion.

Reads promotion_profile.json and renders platform-tailored promotional content
for Reddit, Hacker News, Discord, and X (Twitter). Each template follows the
platform's cultural norms and posting rules to maximize engagement while
avoiding the common pitfalls that get posts flagged or ignored.

Part of the blog-agent framework by NTUNE1030.

Usage:
    from core.promotion import PromotionEngine

    engine = PromotionEngine()
    reddit_post = engine.render("reddit")
    hn_post = engine.render("hacker_news")
    discord_msg = engine.render("discord")
    tweet = engine.render("twitter")
    all_posts = engine.render_all()
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Optional

from core.config import Colors, logger, CURRENT_SESSION_DIR, get_current_session_reports_dir


# ─── Platform Template Constants ────────────────────────────────────────────

REDDIT_TEMPLATE = """Hey everyone,

I recently built a framework called {project_name} (primarily using {tech_stack}) to solve some of the automation bottlenecks I was hitting during vulnerability scanning and payload analysis.

**What it does:**
{project_description}

**How it's built:**
{how_its_built}

**Why I made it:**
{problem_solved} It's built to drop into restricted environments.

Source: {repo_url}

I'd love to get feedback from anyone currently managing external exposure or running automated red team infrastructure. Are there specific tool integrations you feel are missing?"""

REDDIT_TITLE_TEMPLATE = "[Tool] {project_name}: A framework integrating local/cloud LLMs with standard red teaming tools"

HACKER_NEWS_TITLE_TEMPLATE = "Show HN: {project_name} – An LLM framework for automating vulnerability scanning"

HACKER_NEWS_COMMENT_TEMPLATE = """Hi HN,

I'm {author_name}, {author_title}. I built {project_name} to scratch my own itch while studying penetration testing.

The specific problem I hit was {problem_solved_lower}. {project_name} uses local or cloud LLMs to parse output from standard Kali tools and automate the decision-making loop.

Technical constraints:
{technical_challenge}

It's still in active development, but the core looping mechanism works. Would love feedback on the architecture, specifically around how you handle tool-chaining in automated environments.

Repo: {repo_url}"""

DISCORD_TEMPLATE = """Hey all, I just open-sourced a tool I've been working on called **{project_name}**.

It's a {tech_stack_inline} framework that hooks LLMs into standard cybersecurity tools to automate vulnerability scanning and malware analysis. If you are grinding CTFs or doing automated red teaming, it might save you some time parsing outputs.

```
{key_features_inline}
```

Check it out here: {repo_url}
Let me know if you break it or have feature requests{good_first_issues_note}!"""

TWITTER_TEMPLATE = """🚀 {project_name} — {tech_stack_inline} framework bridging LLMs with cybersecurity tools. Automate vuln scanning with human-in-the-loop control.

{repo_url}

#infosec #redteaming #opensource"""

TWITTER_THREAD_TEMPLATE = """🧵 Thread: {project_name}

{project_name} bridges local/cloud LLMs with standard cybersecurity tools. Instead of manually piping outputs between scans, the agent interprets results and chains the next logical step.

1/{thread_count}"""

TWITTER_THREAD_TWEET = """{project_name} — {thread_point}

{thread_hashtag}"""

TWITTER_THREAD_FINAL = """Built with {tech_stack_inline}. HITL auth ensures you never lose control over execution.

{repo_url}

#infosec #redteaming #opensource {thread_count}/{thread_count}"""


class PromotionEngine:
    """Renders platform-specific promotional content from a promotion profile.

    Reads promotion_profile.json for identity, project metadata, and per-platform
    settings. Each render method produces content tailored to the platform's
    cultural norms — Reddit gets deep technical detail, HN gets plain-text
    factual format, Discord gets conversational brevity, and X gets visual hooks
    with hashtags.

    All rendered output is saved to the session's reports/ directory for audit
    trail and future reference.
    """

    def __init__(self, profile_path: Optional[str] = None) -> None:
        """Load the promotion profile from disk.

        Args:
            profile_path: Optional path to promotion_profile.json.
                          Defaults to project root.
        """
        if profile_path is None:
            profile_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "promotion_profile.json"
            )

        try:
            with open(profile_path, "r", encoding="utf-8") as f:
                self.profile: dict[str, Any] = json.load(f)
            print(f"{Colors.GREEN}[*] Loaded Promotion Profile: {self.profile.get('project', {}).get('name', 'Unknown')}{Colors.RESET}")
        except Exception as e:
            logger.error(f"Failed to load promotion_profile.json: {e}")
            self.profile = {
                "identity": {"name": "Developer", "title": "", "bio": "", "github_url": ""},
                "project": {"name": "Project", "tagline": "", "description": "", "tech_stack": [],
                            "key_features": [], "problem_solved": "", "technical_challenge": "",
                            "repo_url": "", "good_first_issues": False, "status": "active_development"},
                "platforms": {}
            }

    # ─── Helper Methods ────────────────────────────────────────────────

    def _get(self, path: str, default: Any = "") -> Any:
        """Dot-notation accessor for nested profile keys.

        Example: _get('identity.name') returns profile['identity']['name']
        """
        keys = path.split(".")
        obj = self.profile
        for key in keys:
            if isinstance(obj, dict) and key in obj:
                obj = obj[key]
            else:
                return default
        return obj

    def _tech_stack_str(self) -> str:
        """Format tech stack as a readable string: 'Python, Go & Bash'"""
        stack = self._get("project.tech_stack", [])
        if not stack:
            return "Python"
        if len(stack) == 1:
            return stack[0]
        return ", ".join(stack[:-1]) + " & " + stack[-1]

    def _tech_stack_inline(self) -> str:
        """Format tech stack inline: 'Python/Go/Bash'"""
        stack = self._get("project.tech_stack", [])
        return "/".join(stack) if stack else "Python"

    def _key_features_bullets(self) -> str:
        """Format key features as Reddit-style bullet list."""
        features = self._get("project.key_features", [])
        if not features:
            return "- Core framework functionality"
        return "\n".join(f"- {f}" for f in features)

    def _key_features_inline(self) -> str:
        """Format key features as compact inline list for Discord code blocks."""
        features = self._get("project.key_features", [])
        if not features:
            return "Core framework functionality"
        return "\n".join(f"  • {f}" for f in features[:5])

    def _how_its_built(self) -> str:
        """Build the 'How it's built' section for Reddit from profile data."""
        stack = self._get("project.tech_stack", [])
        features = self._get("project.key_features", [])

        parts = []
        if "Python" in stack and "Go" in stack:
            parts.append("Written in Python with Go modules for performance-critical parsing.")
        elif "Python" in stack:
            parts.append("Written in Python for maximum flexibility and rapid prototyping.")
        elif "Go" in stack:
            parts.append("Written in Go for performance and single-binary deployment.")

        # Look for zero-dep and bash features
        for feat in features:
            feat_lower = feat.lower()
            if "zero runtime" in feat_lower or "zero dep" in feat_lower:
                parts.append("Zero runtime dependencies for the core execution engine.")
            if "bash" in feat_lower:
                parts.append("Integrates directly with Bash for system-level tool execution.")
            if "hitl" in feat_lower or "human-in-the-loop" in feat_lower:
                parts.append("Human-In-The-Loop (HITL) authorization ensures safe automation.")

        if not parts:
            parts.append(f"Built with {self._tech_stack_str()}.")

        return "\n".join(parts)

    def _good_first_issues_note(self) -> str:
        """Generate the good-first-issues note for Discord."""
        has_issues = self._get("project.good_first_issues", False)
        if has_issues:
            return "—I tagged a few easy bugs as good first issue if anyone is looking to contribute"
        return ""

    # ─── Platform Renderers ────────────────────────────────────────────

    def render_reddit(self) -> dict[str, str]:
        """Render promotional content for Reddit (r/netsec, r/HowToHack, r/Python).

        Reddit Golden Rule: Redditors aggressively reject marketing. Focus
        entirely on technical architecture, the problem solved, and how it
        works under the hood.

        Returns:
            Dict with 'title' and 'body' keys.
        """
        if not self._get("platforms.reddit.enabled", False):
            return {"title": "[DISABLED]", "body": "Reddit promotion is disabled in promotion_profile.json"}

        title = REDDIT_TITLE_TEMPLATE.format(
            project_name=self._get("project.name", "MyProject")
        )

        body = REDDIT_TEMPLATE.format(
            project_name=self._get("project.name", "MyProject"),
            tech_stack=self._tech_stack_str(),
            project_description=self._get("project.description", ""),
            how_its_built=self._how_its_built(),
            problem_solved=self._get("project.problem_solved", ""),
            repo_url=self._get("project.repo_url", "[Insert GitHub Link]")
        )

        subreddits = self._get("platforms.reddit.subreddits", [])
        posting_rules = self._get("platforms.reddit.posting_rules", "")

        logger.info("Rendered Reddit promotion post")
        return {
            "title": title,
            "body": body,
            "subreddits": subreddits,
            "posting_rules": posting_rules,
            "platform": "reddit"
        }

    def render_hacker_news(self) -> dict[str, str]:
        """Render promotional content for Hacker News (Show HN).

        HN Golden Rule: Show HN is strictly for things people can play with
        right now. Titles must be strictly factual with no hype words. The
        first comment should explain technical trade-offs and background.

        Returns:
            Dict with 'title' and 'first_comment' keys.
        """
        if not self._get("platforms.hacker_news.enabled", False):
            return {"title": "[DISABLED]", "first_comment": "Hacker News promotion is disabled"}

        title = HACKER_NEWS_TITLE_TEMPLATE.format(
            project_name=self._get("project.name", "MyProject")
        )

        problem_solved = self._get("project.problem_solved", "")
        # Lower-case first char for inline usage
        problem_solved_lower = problem_solved[0].lower() + problem_solved[1:] if problem_solved else ""

        first_comment = HACKER_NEWS_COMMENT_TEMPLATE.format(
            author_name=self._get("identity.name", "the author"),
            author_title=self._get("identity.title", ""),
            project_name=self._get("project.name", "MyProject"),
            problem_solved_lower=problem_solved_lower,
            technical_challenge=self._get("project.technical_challenge", ""),
            repo_url=self._get("project.repo_url", "[Insert Link]")
        )

        posting_rules = self._get("platforms.hacker_news.posting_rules", "")

        logger.info("Rendered Hacker News Show HN post")
        return {
            "title": title,
            "first_comment": first_comment,
            "posting_rules": posting_rules,
            "platform": "hacker_news"
        }

    def render_discord(self) -> dict[str, str]:
        """Render promotional content for Discord (Security/CTF servers).

        Discord Golden Rule: Keep it conversational, brief, and immediately
        state what is in it for them. Use code blocks for formatting.

        Returns:
            Dict with 'message' key.
        """
        if not self._get("platforms.discord.enabled", False):
            return {"message": "[DISABLED] Discord promotion is disabled", "platform": "discord"}

        message = DISCORD_TEMPLATE.format(
            project_name=self._get("project.name", "MyProject"),
            tech_stack_inline=self._tech_stack_inline(),
            key_features_inline=self._key_features_inline(),
            repo_url=self._get("project.repo_url", "[Insert Link]"),
            good_first_issues_note=self._good_first_issues_note()
        )

        servers = self._get("platforms.discord.servers", [])
        posting_rules = self._get("platforms.discord.posting_rules", "")

        logger.info("Rendered Discord promotion message")
        return {
            "message": message,
            "servers": servers,
            "posting_rules": posting_rules,
            "platform": "discord"
        }

    def render_twitter(self) -> dict[str, Any]:
        """Render promotional content for X / Twitter.

        Twitter Golden Rule: Visuals are mandatory. Attach a 1280x640 social
        preview image or a 15-second screen recording/GIF. Use hashtags.

        If the single tweet exceeds 280 characters, automatically generates
        a thread (multiple tweets) instead.

        Returns:
            Dict with 'tweet' (single tweet or first tweet of thread),
            'thread' (list of tweets if thread mode), 'media_note',
            'character_count', 'posting_rules', and 'platform' keys.
        """
        if not self._get("platforms.twitter.enabled", False):
            return {"tweet": "[DISABLED] Twitter promotion is disabled", "platform": "twitter"}

        tweet = TWITTER_TEMPLATE.format(
            project_name=self._get("project.name", "MyProject"),
            tech_stack_inline=self._tech_stack_inline(),
            repo_url=self._get("project.repo_url", "[Insert GitHub Link]")
        )

        posting_rules = self._get("platforms.twitter.posting_rules", "")
        result: dict[str, Any] = {
            "media_note": "⚠️ REQUIRED: Attach a 1280x640 social preview image or 15-second terminal GIF before posting.",
            "posting_rules": posting_rules,
            "platform": "twitter"
        }

        # If single tweet fits, use it; otherwise build a thread
        if len(tweet) <= 280:
            result["tweet"] = tweet
            result["character_count"] = len(tweet)
            result["is_thread"] = False
            result["thread"] = [tweet]
        else:
            # Build a thread from profile data
            key_features = self._get("project.key_features", [])
            # Pick top 3 features for thread points
            thread_points = key_features[:3] if key_features else ["Bridges LLMs with cybersecurity tools"]
            thread_count = 2 + len(thread_points)  # opener + feature tweets + closer

            thread_tweets: list[str] = []

            # Tweet 1: Opener
            opener = TWITTER_THREAD_TEMPLATE.format(
                project_name=self._get("project.name", "MyProject"),
                tech_stack_inline=self._tech_stack_inline(),
                thread_count=thread_count
            )
            # Truncate opener to 280 if needed
            if len(opener) > 280:
                opener = opener[:277] + "..."
            thread_tweets.append(opener)

            # Feature tweets
            for i, point in enumerate(thread_points, start=2):
                feature_tweet = TWITTER_THREAD_TWEET.format(
                    project_name=self._get("project.name", "MyProject"),
                    thread_point=point,
                    thread_hashtag="#infosec"
                )
                if len(feature_tweet) > 280:
                    feature_tweet = feature_tweet[:277] + "..."
                thread_tweets.append(feature_tweet)

            # Final tweet: link + hashtags
            final = TWITTER_THREAD_FINAL.format(
                tech_stack_inline=self._tech_stack_inline(),
                repo_url=self._get("project.repo_url", "[Insert GitHub Link]"),
                thread_count=thread_count
            )
            if len(final) > 280:
                final = final[:277] + "..."
            thread_tweets.append(final)

            result["tweet"] = thread_tweets[0]  # First tweet for preview
            result["thread"] = thread_tweets
            result["character_count"] = len(thread_tweets[0])
            result["is_thread"] = True
            result["thread_count"] = len(thread_tweets)

        logger.info(f"Rendered Twitter/X promotion ({'thread' if result.get('is_thread') else 'single tweet'})")
        return result

    # ─── Aggregate Renderers ───────────────────────────────────────────

    def render(self, platform: str) -> dict[str, Any]:
        """Render promotional content for a specific platform.

        Args:
            platform: One of 'reddit', 'hacker_news', 'discord', 'twitter'

        Returns:
            Platform-specific dict with rendered content.

        Raises:
            ValueError: If platform name is not recognized.
        """
        renderers = {
            "reddit": self.render_reddit,
            "hacker_news": self.render_hacker_news,
            "discord": self.render_discord,
            "twitter": self.render_twitter,
        }

        platform_key = platform.lower().replace(" ", "_")
        if platform_key not in renderers:
            valid = ", ".join(renderers.keys())
            raise ValueError(f"Unknown platform '{platform}'. Valid platforms: {valid}")

        return renderers[platform_key]()

    def render_all(self) -> dict[str, dict[str, Any]]:
        """Render promotional content for all enabled platforms.

        Returns:
            Dict mapping platform name to rendered content dict.
        """
        results = {}
        for platform in ["reddit", "hacker_news", "discord", "twitter"]:
            result = self.render(platform)
            if result.get("platform") != "[DISABLED]":
                results[platform] = result
        return results

    def render_all_to_files(self, output_dir: Optional[str] = None) -> dict[str, str]:
        """Render all platforms and save to individual markdown files.

        Args:
            output_dir: Directory to write files to. Defaults to the
                       current session's reports/ directory.

        Returns:
            Dict mapping platform name to output file path.
        """
        if output_dir is None:
            output_dir = get_current_session_reports_dir()

        os.makedirs(output_dir, exist_ok=True)
        all_content = self.render_all()
        output_files = {}

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        for platform, content in all_content.items():
            filename = f"promotion_{platform}_{timestamp}.md"
            filepath = os.path.join(output_dir, filename)

            md_content = self._format_as_markdown(platform, content)

            try:
                with open(filepath, 'w', encoding='utf-8') as f:
                    f.write(md_content)
                output_files[platform] = filepath
                logger.info(f"Saved {platform} promotion to {filepath}")
            except Exception as e:
                logger.error(f"Failed to save {platform} promotion: {e}")
                output_files[platform] = f"[ERROR: {e}]"

        # Also save a combined summary
        summary_file = os.path.join(output_dir, f"promotion_ALL_{timestamp}.md")
        try:
            with open(summary_file, 'w', encoding='utf-8') as f:
                f.write(f"# Promotion Content — All Platforms\n")
                f.write(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"Project: {self._get('project.name', 'Unknown')}\n\n---\n\n")
                for platform, content in all_content.items():
                    f.write(self._format_as_markdown(platform, content))
                    f.write("\n\n---\n\n")
            output_files["ALL"] = summary_file
        except Exception as e:
            logger.error(f"Failed to save combined promotion file: {e}")

        return output_files

    def _format_as_markdown(self, platform: str, content: dict[str, Any]) -> str:
        """Format a rendered platform dict as readable markdown.

        Args:
            platform: Platform name (e.g., 'reddit')
            content: Rendered content dict from render_* method

        Returns:
            Markdown-formatted string
        """
        platform_display = platform.replace("_", " ").title()
        lines = [f"## {platform_display}\n"]

        if platform == "reddit":
            lines.append(f"### Title\n{content.get('title', '')}\n")
            lines.append(f"### Body\n{content.get('body', '')}\n")
            if content.get("subreddits"):
                lines.append(f"### Target Subreddits\n{', '.join(content['subreddits'])}\n")
            if content.get("posting_rules"):
                lines.append(f"### Posting Rules\n{content['posting_rules']}\n")

        elif platform == "hacker_news":
            lines.append(f"### Title\n{content.get('title', '')}\n")
            lines.append(f"### Required First Comment\n{content.get('first_comment', '')}\n")
            if content.get("posting_rules"):
                lines.append(f"### Posting Rules\n{content['posting_rules']}\n")

        elif platform == "discord":
            lines.append(f"### Message\n{content.get('message', '')}\n")
            if content.get("servers"):
                lines.append(f"### Target Servers\n{', '.join(content['servers'])}\n")
            if content.get("posting_rules"):
                lines.append(f"### Posting Rules\n{content['posting_rules']}\n")

        elif platform == "twitter":
            lines.append(f"### Tweet\n{content.get('tweet', '')}\n")
            if content.get("media_note"):
                lines.append(f"### Media\n{content['media_note']}\n")
            if content.get("character_count"):
                lines.append(f"### Character Count\n{content['character_count']}\n")
            if content.get("posting_rules"):
                lines.append(f"### Posting Rules\n{content['posting_rules']}\n")

        return "\n".join(lines)

    def get_platform_summary(self) -> str:
        """Return a human-readable summary of all platform configurations.

        Returns:
            Formatted string showing which platforms are enabled and their settings.
        """
        lines = [f"{Colors.CYAN}📋 Promotion Profile Summary{Colors.RESET}"]
        lines.append(f"  Project: {Colors.GREEN}{self._get('project.name')}{Colors.RESET}")
        lines.append(f"  Author:  {self._get('identity.name')} — {self._get('identity.title')}")
        lines.append(f"  Repo:    {self._get('project.repo_url') or '[NOT SET]'}")
        lines.append(f"  Stack:   {self._tech_stack_str()}")
        lines.append("")
        lines.append(f"  {Colors.CYAN}Platforms:{Colors.RESET}")

        platform_status = {
            "reddit": self._get("platforms.reddit.enabled", False),
            "hacker_news": self._get("platforms.hacker_news.enabled", False),
            "discord": self._get("platforms.discord.enabled", False),
            "twitter": self._get("platforms.twitter.enabled", False),
        }

        for name, enabled in platform_status.items():
            status = f"{Colors.GREEN}ON{Colors.RESET}" if enabled else f"{Colors.RED}OFF{Colors.RESET}"
            lines.append(f"    {name.replace('_', ' ').title()}: {status}")

        return "\n".join(lines)