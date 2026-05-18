"""
Promotion Agent — Standalone CLI for generating and posting platform-specific promotional content.

Renders tailored promotional posts for Reddit, Hacker News, Discord, and X/Twitter
from a single promotion_profile.json configuration. Each platform's content follows
its cultural norms and posting rules to maximize engagement.

Supports both content generation (preview/save) and direct posting via platform APIs
(Reddit via PRAW, Twitter via Tweepy, Discord via webhooks). All posting operations
are CRITICAL HITL risk and require explicit "YES" confirmation.

Git Scan Mode: Scans your GitHub repos and rotates through them so each promotion
cycle features a different project. Uses the GitHub REST API to discover repos and
persists rotation state in memory/git_rotation.json.

Usage:
    # Preview all platforms
    python promote.py

    # Preview a specific platform
    python promote.py --platform reddit

    # Save rendered content to files
    python promote.py --save

    # Scan GitHub repos and promote the next one in rotation
    python promote.py --scan

    # Scan and save the rendered content
    python promote.py --scan --save

    # Scan and promote to a specific platform
    python promote.py --scan --platform discord

    # List repos in the rotation
    python promote.py --scan --list

    # Reset the rotation (all repos become eligible again)
    python promote.py --scan --reset

    # Check which platforms are ready to post
    python promote.py --readiness

    # Dry-run posting (preview what would be posted, no actual posts)
    python promote.py --post

    # Actually post to all configured platforms (CRITICAL — requires confirmation)
    python promote.py --post --confirm

    # Post to a specific platform only
    python promote.py --post --platform reddit --confirm

    # Show platform-specific posting tips
    python promote.py --tips --platform discord

    # Update a profile field before rendering
    python promote.py --set project.repo_url=https://github.com/user/repo

    # Show profile summary
    python promote.py --summary
"""

import os
import sys
import argparse

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')  # type: ignore[attr-defined]

from core.config import Colors
from core.promotion import PromotionEngine


def parse_args():
    """Parse command-line arguments for the promotion agent."""
    parser = argparse.ArgumentParser(
        description="Promotion Agent — Generate and post platform-specific promotional content for your Git profile",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python promote.py                          Preview all platforms
  python promote.py --platform reddit         Preview Reddit post
  python promote.py --save                   Save all to markdown files
  python promote.py --scan                   Scan GitHub & promote next repo
  python promote.py --scan --save            Scan, promote, and save to files
  python promote.py --scan --list            List repos in rotation
  python promote.py --scan --reset           Reset rotation state
  python promote.py --readiness              Check which platforms can post
  python promote.py --post                   Dry-run posting (no actual posts)
  python promote.py --post --confirm         Actually post to all platforms
  python promote.py --post --platform reddit --confirm  Post to Reddit only
  python promote.py --tips --platform discord Show Discord posting tips
  python promote.py --set project.repo_url=https://github.com/user/repo
  python promote.py --summary                 Show profile configuration
        """
    )
    parser.add_argument(
        "--platform", "-p",
        choices=["reddit", "hacker_news", "discord", "twitter", "all"],
        default="all",
        help="Target platform (default: all)"
    )
    parser.add_argument(
        "--save", "-s",
        action="store_true",
        help="Save rendered content to markdown files in session reports/"
    )
    parser.add_argument(
        "--tips", "-t",
        action="store_true",
        help="Show platform-specific posting tips and rules"
    )
    parser.add_argument(
        "--set",
        action="append",
        metavar="FIELD=VALUE",
        help="Update a profile field (dot-notation, e.g., project.repo_url=https://...)"
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="Show current promotion profile summary"
    )
    parser.add_argument(
        "--post",
        action="store_true",
        help="Post content to platforms (dry-run unless --confirm is added)"
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Confirm actual posting (without this flag, --post is a dry-run only)"
    )
    parser.add_argument(
        "--readiness",
        action="store_true",
        help="Check which platforms have API credentials configured and are ready to post"
    )
    parser.add_argument(
        "--scan",
        action="store_true",
        help="Scan GitHub repos and promote the next repo in rotation"
    )
    parser.add_argument(
        "--scan-user",
        metavar="USERNAME",
        default="",
        help="GitHub username to scan (defaults to git_scan.username in profile)"
    )
    parser.add_argument(
        "--scan-list",
        action="store_true",
        help="List all repos in the rotation with their promotion history"
    )
    parser.add_argument(
        "--scan-reset",
        action="store_true",
        help="Reset the promotion rotation so all repos are eligible again"
    )
    return parser.parse_args()


def apply_field_updates(engine: PromotionEngine, updates: list[str]) -> None:
    """Apply --set field updates to the promotion profile.

    Args:
        engine: PromotionEngine instance
        updates: List of "field=value" strings
    """
    import json

    profile_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "promotion_profile.json"
    )

    try:
        with open(profile_path, 'r', encoding='utf-8') as f:
            profile = json.load(f)
    except Exception as e:
        print(f"{Colors.RED}[!] Failed to load promotion_profile.json: {e}{Colors.RESET}")
        return

    for update in updates:
        if '=' not in update:
            print(f"{Colors.RED}[!] Invalid --set format: '{update}'. Use field=value{Colors.RESET}")
            continue

        field, value = update.split('=', 1)
        keys = field.split('.')
        obj = profile

        try:
            for key in keys[:-1]:
                if key not in obj or not isinstance(obj[key], dict):
                    raise KeyError(key)
                obj = obj[key]

            final_key = keys[-1]
            if final_key not in obj:
                print(f"{Colors.YELLOW}[!] Field '{field}' not found in profile. Skipping.{Colors.RESET}")
                continue

            # Try to parse as JSON (for lists, bools, numbers)
            parsed_value = value
            try:
                parsed_value = json.loads(value)
            except (json.JSONDecodeError, ValueError):
                pass

            old_value = obj[final_key]
            obj[final_key] = parsed_value
            print(f"{Colors.GREEN}  ✅ {field}: {old_value} → {parsed_value}{Colors.RESET}")

        except KeyError as e:
            print(f"{Colors.RED}[!] Invalid field path: '{field}' — key '{e}' not found{Colors.RESET}")

    # Save updated profile
    try:
        with open(profile_path, 'w', encoding='utf-8') as f:
            json.dump(profile, f, indent=2, ensure_ascii=False)
        print(f"{Colors.GRAY}[*] Profile saved to promotion_profile.json{Colors.RESET}")
    except Exception as e:
        print(f"{Colors.RED}[!] Failed to save profile: {e}{Colors.RESET}")


def main():
    """Main entry point for the promotion agent CLI."""
    args = parse_args()

    print("=" * 55)
    print("       📣 PROMOTION AGENT — Git Profile Promoter       ")
    print("=" * 55)

    # Handle --set updates first
    if args.set:
        print(f"\n{Colors.CYAN}📝 Updating promotion profile...{Colors.RESET}")
        engine = PromotionEngine()
        apply_field_updates(engine, args.set)
        # Reload engine after updates
        engine = PromotionEngine()
        print()

    # Handle --summary
    if args.summary:
        engine = PromotionEngine()
        print()
        print(engine.get_platform_summary())
        return

    # Handle --readiness
    if args.readiness:
        from tools.posting_ops import check_posting_readiness
        print(check_posting_readiness())
        return

    # Handle --scan
    if args.scan:
        from tools.git_scan_ops import scan_git_repos, get_next_repo_to_promote, inject_repo_to_promote, list_promoted_repos, reset_rotation

        # --scan-list: just list rotation state
        if args.scan_list:
            print()
            print(list_promoted_repos())
            return

        # --scan-reset: clear rotation history
        if args.scan_reset:
            print()
            print(reset_rotation())
            return

        # Full scan → inject → render workflow
        print(f"\n{Colors.CYAN}🔍 Scanning GitHub repos...{Colors.RESET}")
        scan_result = scan_git_repos(args.scan_user)
        print(scan_result)
        print()

        # Select next repo
        print(f"{Colors.CYAN}🎯 Selecting next repo to promote...{Colors.RESET}")
        next_result = get_next_repo_to_promote()
        print(next_result)
        print()

        # Inject into engine
        print(f"{Colors.CYAN}💉 Injecting repo data into promotion engine...{Colors.RESET}")
        inject_result = inject_repo_to_promote()
        print(inject_result)
        print()

        # Create engine (now loaded with injected repo data) and render
        engine = PromotionEngine()

        # Show profile summary for the injected repo
        print(engine.get_platform_summary())
        print()

        # Handle --save with --scan
        if args.save:
            if args.platform == "all":
                output_files = engine.render_all_to_files()
            else:
                try:
                    content = engine.render(args.platform)
                except ValueError as e:
                    print(f"{Colors.RED}[!] {e}{Colors.RESET}")
                    return

                from core.config import get_current_session_reports_dir
                from datetime import datetime
                report_dir = get_current_session_reports_dir()
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"promotion_{args.platform}_{timestamp}.md"
                filepath = os.path.join(report_dir, filename)

                md_content = engine._format_as_markdown(args.platform, content)
                try:
                    with open(filepath, 'w', encoding='utf-8') as f:
                        f.write(md_content)
                    output_files = {args.platform: filepath}
                except Exception as e:
                    print(f"{Colors.RED}[!] Failed to save: {e}{Colors.RESET}")
                    return

            print(f"{Colors.GREEN}✅ Promotion content saved:{Colors.RESET}")
            for plat, path in output_files.items():
                print(f"  {plat.replace('_', ' ').title()}: {path}")
            return

        # Default: preview rendered content for the scanned repo
        if args.platform == "all":
            all_content = engine.render_all()

            if not all_content:
                print(f"{Colors.YELLOW}[!] No platforms enabled in promotion_profile.json{Colors.RESET}")
                return

            for platform, content in all_content.items():
                platform_display = platform.replace("_", " ").title()
                print(f"\n{Colors.CYAN}{'─' * 50}{Colors.RESET}")
                print(f"{Colors.GREEN}▶ {platform_display}{Colors.RESET}")
                print(f"{Colors.CYAN}{'─' * 50}{Colors.RESET}")

                if platform == "reddit":
                    print(f"{Colors.YELLOW}Title:{Colors.RESET} {content.get('title', '')}")
                    print()
                    print(content.get('body', ''))
                    if content.get("subreddits"):
                        print(f"\n{Colors.GRAY}Target: {', '.join(content['subreddits'])}{Colors.RESET}")
                    if content.get("posting_rules"):
                        print(f"{Colors.YELLOW}⚠️  {content['posting_rules']}{Colors.RESET}")

                elif platform == "hacker_news":
                    print(f"{Colors.YELLOW}Title:{Colors.RESET} {content.get('title', '')}")
                    print()
                    print(f"{Colors.YELLOW}Required First Comment:{Colors.RESET}")
                    print(content.get('first_comment', ''))
                    if content.get("posting_rules"):
                        print(f"\n{Colors.YELLOW}⚠️  {content['posting_rules']}{Colors.RESET}")

                elif platform == "discord":
                    print(content.get('message', ''))
                    if content.get("servers"):
                        print(f"\n{Colors.GRAY}Target: {', '.join(content['servers'])}{Colors.RESET}")
                    if content.get("posting_rules"):
                        print(f"{Colors.YELLOW}⚠️  {content['posting_rules']}{Colors.RESET}")

                elif platform == "twitter":
                    if content.get("is_thread"):
                        thread = content.get("thread", [])
                        print(f"{Colors.CYAN}🧵 Thread ({len(thread)} tweets):{Colors.RESET}")
                        for idx, t in enumerate(thread, 1):
                            print(f"\n{Colors.GRAY}--- Tweet {idx}/{len(thread)} ({len(t)} chars) ---{Colors.RESET}")
                            print(t)
                    else:
                        print(content.get('tweet', ''))
                    if content.get("media_note"):
                        print(f"\n{Colors.YELLOW}{content['media_note']}{Colors.RESET}")
                    if content.get("character_count"):
                        print(f"{Colors.GRAY}Characters: {content['character_count']}{Colors.RESET}")
                    if content.get("posting_rules"):
                        print(f"{Colors.YELLOW}⚠️  {content['posting_rules']}{Colors.RESET}")

                print()
        else:
            try:
                content = engine.render(args.platform)
            except ValueError as e:
                print(f"{Colors.RED}[!] {e}{Colors.RESET}")
                return

            platform_display = args.platform.replace("_", " ").title()
            print(f"\n{Colors.CYAN}{'═' * 50}{Colors.RESET}")
            print(f"{Colors.GREEN}▶ {platform_display}{Colors.RESET}")
            print(f"{Colors.CYAN}{'═' * 50}{Colors.RESET}\n")

            if args.platform == "reddit":
                print(f"{Colors.YELLOW}Title:{Colors.RESET} {content.get('title', '')}")
                print()
                print(content.get('body', ''))
            elif args.platform == "hacker_news":
                print(f"{Colors.YELLOW}Title:{Colors.RESET} {content.get('title', '')}")
                print()
                print(f"{Colors.YELLOW}Required First Comment:{Colors.RESET}")
                print(content.get('first_comment', ''))
            elif args.platform == "discord":
                print(content.get('message', ''))
            elif args.platform == "twitter":
                if content.get("is_thread"):
                    thread = content.get("thread", [])
                    print(f"{Colors.CYAN}🧵 Thread ({len(thread)} tweets):{Colors.RESET}")
                    for idx, t in enumerate(thread, 1):
                        print(f"\n{Colors.GRAY}--- Tweet {idx}/{len(thread)} ({len(t)} chars) ---{Colors.RESET}")
                        print(t)
                else:
                    print(content.get('tweet', ''))
                if content.get("media_note"):
                    print(f"\n{Colors.YELLOW}{content['media_note']}{Colors.RESET}")

            print()

        print(f"{Colors.GRAY}💡 Use --scan --save to write content to files, --scan --post to post{Colors.RESET}")
        return

    # Handle --post
    if args.post:
        from tools.posting_ops import post_to_all_platforms, post_to_reddit, post_to_discord, post_to_twitter

        if not args.confirm:
            # Dry run — show what would be posted without actually posting
            result = post_to_all_platforms(dry_run=True)
            print()
            print(result)
            print()
            print(f"{Colors.YELLOW}══════════════════════════════════════════════════════{Colors.RESET}")
            print(f"{Colors.YELLOW}This was a DRY RUN — no content was posted.{Colors.RESET}")
            print(f"{Colors.YELLOW}To actually post, add --confirm:{Colors.RESET}")
            print(f"{Colors.GREEN}  python promote.py --post --confirm{Colors.RESET}")
            if args.platform != "all":
                print(f"{Colors.GREEN}  python promote.py --post --platform {args.platform} --confirm{Colors.RESET}")
            print(f"{Colors.YELLOW}══════════════════════════════════════════════════════{Colors.RESET}")
            return

        # ACTUAL POSTING — this is the real deal
        print(f"\n{Colors.RED}{'=' * 55}{Colors.RESET}")
        print(f"{Colors.RED}🚨 CRITICAL: YOU ARE ABOUT TO POST TO PUBLIC PLATFORMS 🚨{Colors.RESET}")
        print(f"{Colors.RED}{'=' * 55}{Colors.RESET}")
        print(f"{Colors.YELLOW}This action is IRREVERSIBLE. Posted content cannot be undone by this tool.{Colors.RESET}")
        print(f"{Colors.YELLOW}Each platform post will require explicit YES confirmation.{Colors.RESET}")
        print()

        if args.platform == "all":
            result = post_to_all_platforms(dry_run=False)
            print(result)
        else:
            # Post to a single platform
            engine = PromotionEngine()
            try:
                content = engine.render(args.platform)
            except ValueError as e:
                print(f"{Colors.RED}[!] {e}{Colors.RESET}")
                return

            if args.platform == "reddit":
                for sub in content.get("subreddits", []):
                    clean_sub = sub.replace("r/", "")
                    result = post_to_reddit(
                        subreddit=clean_sub,
                        title=content.get("title", ""),
                        body=content.get("body", "")
                    )
                    print(f"r/{clean_sub}: {result}")

            elif args.platform == "discord":
                result = post_to_discord(message=content.get("message", ""))
                print(f"Discord: {result}")

            elif args.platform == "twitter":
                media_path = content.get("default_media", "")
                if media_path:
                    import os as _os
                    media_path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), media_path)
                if content.get("is_thread"):
                    result = post_to_twitter(tweet_text="", thread=content.get("thread", []), media_path=media_path)
                else:
                    result = post_to_twitter(tweet_text=content.get("tweet", ""), media_path=media_path)
                print(f"Twitter: {result}")

            elif args.platform == "hacker_news":
                print(f"{Colors.YELLOW}[!] Hacker News has no posting API. Post manually at https://news.ycombinator.com/submit{Colors.RESET}")
                print(f"{Colors.GRAY}Title: {content.get('title', '')}{Colors.RESET}")
                print(f"{Colors.GRAY}First comment: {content.get('first_comment', '')[:200]}...{Colors.RESET}")

        return

    # Handle --tips
    if args.tips:
        from tools.promotion_ops import get_promotion_tips
        platform = args.platform if args.platform != "all" else "reddit"
        print()
        print(get_promotion_tips(platform))
        if args.platform == "all":
            print(f"\n{Colors.GRAY}💡 Use --platform <name> --tips to see tips for a specific platform{Colors.RESET}")
        return

    # Create engine and render
    engine = PromotionEngine()

    # Show profile summary
    print()
    print(engine.get_platform_summary())
    print()

    # Handle --save
    if args.save:
        if args.platform == "all":
            output_files = engine.render_all_to_files()
        else:
            try:
                content = engine.render(args.platform)
            except ValueError as e:
                print(f"{Colors.RED}[!] {e}{Colors.RESET}")
                return

            from core.config import get_current_session_reports_dir
            from datetime import datetime
            report_dir = get_current_session_reports_dir()
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"promotion_{args.platform}_{timestamp}.md"
            filepath = os.path.join(report_dir, filename)

            md_content = engine._format_as_markdown(args.platform, content)
            try:
                with open(filepath, 'w', encoding='utf-8') as f:
                    f.write(md_content)
                output_files = {args.platform: filepath}
            except Exception as e:
                print(f"{Colors.RED}[!] Failed to save: {e}{Colors.RESET}")
                return

        print(f"{Colors.GREEN}✅ Promotion content saved:{Colors.RESET}")
        for plat, path in output_files.items():
            print(f"  {plat.replace('_', ' ').title()}: {path}")
        return

    # Default: preview rendered content
    if args.platform == "all":
        all_content = engine.render_all()

        if not all_content:
            print(f"{Colors.YELLOW}[!] No platforms enabled in promotion_profile.json{Colors.RESET}")
            print(f"{Colors.GRAY}    Edit promotion_profile.json and set platforms.*.enabled to true{Colors.RESET}")
            return

        for platform, content in all_content.items():
            platform_display = platform.replace("_", " ").title()
            print(f"\n{Colors.CYAN}{'─' * 50}{Colors.RESET}")
            print(f"{Colors.GREEN}▶ {platform_display}{Colors.RESET}")
            print(f"{Colors.CYAN}{'─' * 50}{Colors.RESET}")

            if platform == "reddit":
                print(f"{Colors.YELLOW}Title:{Colors.RESET} {content.get('title', '')}")
                print()
                print(content.get('body', ''))
                if content.get("subreddits"):
                    print(f"\n{Colors.GRAY}Target: {', '.join(content['subreddits'])}{Colors.RESET}")
                if content.get("posting_rules"):
                    print(f"{Colors.YELLOW}⚠️  {content['posting_rules']}{Colors.RESET}")

            elif platform == "hacker_news":
                print(f"{Colors.YELLOW}Title:{Colors.RESET} {content.get('title', '')}")
                print()
                print(f"{Colors.YELLOW}Required First Comment:{Colors.RESET}")
                print(content.get('first_comment', ''))
                if content.get("posting_rules"):
                    print(f"\n{Colors.YELLOW}⚠️  {content['posting_rules']}{Colors.RESET}")

            elif platform == "discord":
                print(content.get('message', ''))
                if content.get("servers"):
                    print(f"\n{Colors.GRAY}Target: {', '.join(content['servers'])}{Colors.RESET}")
                if content.get("posting_rules"):
                    print(f"{Colors.YELLOW}⚠️  {content['posting_rules']}{Colors.RESET}")

            elif platform == "twitter":
                if content.get("is_thread"):
                    thread = content.get("thread", [])
                    print(f"{Colors.CYAN}🧵 Thread ({len(thread)} tweets):{Colors.RESET}")
                    for idx, t in enumerate(thread, 1):
                        print(f"\n{Colors.GRAY}--- Tweet {idx}/{len(thread)} ({len(t)} chars) ---{Colors.RESET}")
                        print(t)
                else:
                    print(content.get('tweet', ''))
                if content.get("media_note"):
                    print(f"\n{Colors.YELLOW}{content['media_note']}{Colors.RESET}")
                if content.get("character_count"):
                    print(f"{Colors.GRAY}Characters: {content['character_count']}{Colors.RESET}")
                if content.get("posting_rules"):
                    print(f"{Colors.YELLOW}⚠️  {content['posting_rules']}{Colors.RESET}")

            print()
    else:
        # Single platform preview
        try:
            content = engine.render(args.platform)
        except ValueError as e:
            print(f"{Colors.RED}[!] {e}{Colors.RESET}")
            return

        platform_display = args.platform.replace("_", " ").title()
        print(f"\n{Colors.CYAN}{'═' * 50}{Colors.RESET}")
        print(f"{Colors.GREEN}▶ {platform_display}{Colors.RESET}")
        print(f"{Colors.CYAN}{'═' * 50}{Colors.RESET}\n")

        if args.platform == "reddit":
            print(f"{Colors.YELLOW}Title:{Colors.RESET} {content.get('title', '')}")
            print()
            print(content.get('body', ''))
            if content.get("subreddits"):
                print(f"\n{Colors.GRAY}Target Subreddits: {', '.join(content['subreddits'])}{Colors.RESET}")
            if content.get("posting_rules"):
                print(f"{Colors.YELLOW}⚠️  {content['posting_rules']}{Colors.RESET}")

        elif args.platform == "hacker_news":
            print(f"{Colors.YELLOW}Title:{Colors.RESET} {content.get('title', '')}")
            print()
            print(f"{Colors.YELLOW}Required First Comment:{Colors.RESET}")
            print(content.get('first_comment', ''))
            if content.get("posting_rules"):
                print(f"\n{Colors.YELLOW}⚠️  {content['posting_rules']}{Colors.RESET}")

        elif args.platform == "discord":
            print(content.get('message', ''))
            if content.get("servers"):
                print(f"\n{Colors.GRAY}Target Servers: {', '.join(content['servers'])}{Colors.RESET}")
            if content.get("posting_rules"):
                print(f"{Colors.YELLOW}⚠️  {content['posting_rules']}{Colors.RESET}")

        elif args.platform == "twitter":
            if content.get("is_thread"):
                thread = content.get("thread", [])
                print(f"{Colors.CYAN}🧵 Thread ({len(thread)} tweets):{Colors.RESET}")
                for idx, t in enumerate(thread, 1):
                    print(f"\n{Colors.GRAY}--- Tweet {idx}/{len(thread)} ({len(t)} chars) ---{Colors.RESET}")
                    print(t)
            else:
                print(content.get('tweet', ''))
            if content.get("media_note"):
                print(f"\n{Colors.YELLOW}{content['media_note']}{Colors.RESET}")
            if content.get("character_count"):
                print(f"{Colors.GRAY}Character count: {content['character_count']}{Colors.RESET}")
            if content.get("posting_rules"):
                print(f"{Colors.YELLOW}⚠️  {content['posting_rules']}{Colors.RESET}")

        print()

    print(f"{Colors.GRAY}💡 Use --save to write content to files, --tips for posting advice{Colors.RESET}")


if __name__ == "__main__":
    main()