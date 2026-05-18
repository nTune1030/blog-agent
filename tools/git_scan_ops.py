"""
Git scan tools — Scan a GitHub user's repos and rotate through them for promotion.

Uses the GitHub REST API (no auth required for public repos) to discover all
public repositories for a configured GitHub user. Tracks which repos have
been promoted in a rotation file so each promotion cycle features a different
repo, avoiding repetitive posts about the same project.

Rotation state is persisted in memory/git_rotation.json so it survives across
sessions and agent restarts.

HITL Risk Classification:
- scan_git_repos: LOW (read-only, public API)
- get_next_repo_to_promote: LOW (read-only, reads local state)
- list_promoted_repos: LOW (read-only, reads local state)
- reset_rotation: MEDIUM (modifies rotation state file)
"""

import json
import os
from datetime import datetime
from typing import Optional

import requests

from core.config import Colors, logger, BASE_DIR, MEMORY_DIR
from core.promotion import PromotionEngine


# ─── Constants ────────────────────────────────────────────────────────────────

GITHUB_API = "https://api.github.com"
ROTATION_FILE = os.path.join(MEMORY_DIR, "git_rotation.json")
DEFAULT_COOLDOWN_DAYS = 7


# ─── Internal Helpers ─────────────────────────────────────────────────────────

def _load_rotation() -> dict:
    """Load the rotation state from disk, or create defaults if missing."""
    if os.path.exists(ROTATION_FILE):
        try:
            with open(ROTATION_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Failed to load git_rotation.json: {e}. Starting fresh.")

    return {
        "repos": {},          # repo_name -> {promoted_at, repo_data}
        "last_scan": None,    # ISO timestamp of last scan
        "scan_count": 0       # total scans performed
    }


def _save_rotation(rotation: dict) -> None:
    """Persist rotation state to disk."""
    os.makedirs(MEMORY_DIR, exist_ok=True)
    try:
        with open(ROTATION_FILE, "w", encoding="utf-8") as f:
            json.dump(rotation, f, indent=2, ensure_ascii=False)
        logger.info("Saved git rotation state")
    except Exception as e:
        logger.error(f"Failed to save git_rotation.json: {e}")


def _get_profile_config() -> dict:
    """Read git_scan config from promotion_profile.json."""
    try:
        profile_path = os.path.join(BASE_DIR, "promotion_profile.json")
        with open(profile_path, "r", encoding="utf-8") as f:
            profile = json.load(f)
        return profile.get("git_scan", {})
    except Exception:
        return {}


def _fetch_readme(owner: str, repo: str) -> str:
    """Fetch the README.md content for a repo via GitHub API.

    Returns the decoded text, or empty string on failure.
    """
    url = f"{GITHUB_API}/repos/{owner}/{repo}/readme"
    headers = {"Accept": "application/vnd.github.v3.raw"}
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            return resp.text[:4000]  # Cap at 4k chars to avoid bloat
    except Exception as e:
        logger.warning(f"Could not fetch README for {owner}/{repo}: {e}")
    return ""


def _extract_repo_metadata(repo_json: dict, readme_text: str) -> dict:
    """Transform GitHub API repo data + README into a promotion-ready dict.

    Maps GitHub repo fields to the promotion profile's project schema
    so it can be injected directly into PromotionEngine. Tech stack is
    derived from the primary language only; GitHub topics are used for
    key features and target audience instead.
    """
    name = repo_json.get("name", "Unknown")
    description = repo_json.get("description") or f"A {repo_json.get('language', 'code')} project by the author."
    language = repo_json.get("language") or "Code"
    topics = repo_json.get("topics", [])
    stars = repo_json.get("stargazers_count", 0)
    forks = repo_json.get("forks_count", 0)
    html_url = repo_json.get("html_url", "")
    is_fork = repo_json.get("fork", False)

    # Tech stack: only the primary language (topics aren't tech stack)
    tech_stack = [language] if language else ["Code"]

    # Derive key features from topics (describe what the project covers)
    key_features = []
    if topics:
        # Group topics into a readable feature list
        for t in topics[:5]:
            key_features.append(f"{t.replace('-', ' ').title()} support")
    if not key_features:
        key_features = [f"Open-source {language} project"]

    # Derive tagline from description
    tagline = description if description else f"An open-source {language} project"

    # Derive problem_solved from description
    problem_solved = description if description else f"A utility {language} project solving real-world problems."

    # Derive technical_challenge
    technical_challenge = f"Built primarily with {language}. "
    if stars > 0:
        technical_challenge += f"Community-validated with {stars} star{'s' if stars != 1 else ''}. "
    technical_challenge += "Designed for practical, real-world use."

    # Determine good_first_issues
    has_issues = repo_json.get("has_issues", False) and repo_json.get("open_issues_count", 0) > 0

    # Build target_audience from topics
    if topics:
        topic_str = ", ".join(t.replace("-", " ") for t in topics[:3])
        target_audience = f"Developers and practitioners in {topic_str}"
    else:
        target_audience = f"Developers working with {language}"

    return {
        "name": name,
        "tagline": tagline,
        "description": description,
        "tech_stack": tech_stack,
        "key_features": key_features,
        "problem_solved": problem_solved,
        "technical_challenge": technical_challenge,
        "target_audience": target_audience,
        "repo_url": html_url,
        "good_first_issues": has_issues,
        "status": "active_development" if repo_json.get("archived", False) is False else "archived",
        "stars": stars,
        "forks": forks,
        "is_fork": is_fork,
        "readme_excerpt": readme_text[:500] if readme_text else "",
        "language": language,
        "topics": topics,
    }


# ─── Agent-Callable Tools ─────────────────────────────────────────────────────

def scan_git_repos(username: str = "") -> str:
    """Scan a GitHub user's public repos and cache them for promotion rotation.

    Fetches all public repositories for the given GitHub user via the REST API,
    extracts metadata and README content, and stores them in the rotation file.
    Skips forked repos by default (configurable in promotion_profile.json).

    Args:
        username: GitHub username to scan. Defaults to the value in
                  promotion_profile.json → git_scan.username, then
                  identity.github_username.

    Returns:
        Formatted summary of discovered repos.
    """
    if not username:
        config = _get_profile_config()
        username = config.get("username", "")
        if not username:
            # Fallback to identity.github_username from promotion profile
            try:
                profile_path = os.path.join(BASE_DIR, "promotion_profile.json")
                with open(profile_path, "r", encoding="utf-8") as f:
                    profile = json.load(f)
                username = profile.get("identity", {}).get("github_username", "")
            except Exception:
                pass

    if not username:
        return f"{Colors.RED}[ERROR] No GitHub username provided and none found in promotion_profile.json{Colors.RESET}"

    config = _get_profile_config()
    exclude_forks = config.get("exclude_forks", True)
    excluded_repos = config.get("excluded_repos", [])

    # Fetch repos from GitHub API
    url = f"{GITHUB_API}/users/{username}/repos"
    params = {"per_page": 100, "sort": "updated", "direction": "desc"}

    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        repos = resp.json()
    except requests.exceptions.HTTPError as e:
        return f"{Colors.RED}[ERROR] GitHub API error: {e}{Colors.RESET}"
    except Exception as e:
        return f"{Colors.RED}[ERROR] Failed to fetch repos: {e}{Colors.RESET}"

    if not repos:
        return f"{Colors.YELLOW}[!] No public repos found for {username}{Colors.RESET}"

    # Load existing rotation state
    rotation = _load_rotation()

    # Process each repo
    new_count = 0
    skipped_forks = 0
    skipped_excluded = 0
    updated_count = 0

    for repo in repos:
        repo_name = repo.get("name", "")

        # Skip excluded repos
        if repo_name in excluded_repos:
            skipped_excluded += 1
            continue

        # Skip forks if configured
        if exclude_forks and repo.get("fork", False):
            skipped_forks += 1
            continue

        # Skip archived repos
        if repo.get("archived", False):
            continue

        # Fetch README for richer metadata
        readme_text = _fetch_readme(username, repo_name)
        repo_data = _extract_repo_metadata(repo, readme_text)

        # Update rotation entry
        if repo_name not in rotation["repos"]:
            new_count += 1
        else:
            updated_count += 1

        # Preserve promoted_at if already promoted; otherwise leave absent
        existing = rotation["repos"].get(repo_name, {})
        rotation["repos"][repo_name] = {
            "promoted_at": existing.get("promoted_at", None),
            "repo_data": repo_data,
        }

    # Update scan metadata
    rotation["last_scan"] = datetime.now().isoformat()
    rotation["scan_count"] = rotation.get("scan_count", 0) + 1
    _save_rotation(rotation)

    total_tracked = len(rotation["repos"])

    output = f"{Colors.GREEN}✅ Git scan complete for {username}{Colors.RESET}\n"
    output += f"  Repos found: {len(repos)}\n"
    output += f"  New repos cached: {new_count}\n"
    output += f"  Updated repos: {updated_count}\n"
    if skipped_forks:
        output += f"  Forks skipped: {skipped_forks}\n"
    if skipped_excluded:
        output += f"  Excluded repos skipped: {skipped_excluded}\n"
    output += f"  Total tracked for rotation: {total_tracked}\n"
    output += f"  {Colors.GRAY}Rotation state saved to {ROTATION_FILE}{Colors.RESET}"

    logger.info(f"Git scan completed for {username}: {total_tracked} repos tracked")
    return output


def get_next_repo_to_promote(cooldown_days: int = 0) -> str:
    """Get the next repo to promote from the rotation, respecting cooldown.

    Selects the repo that was promoted longest ago (or never promoted),
    skipping any repos promoted within the cooldown period. This ensures
    each promotion cycle features a different project.

    Args:
        cooldown_days: Minimum days between promotions of the same repo.
                       Defaults to git_scan.cooldown_days in promotion_profile.json,
                       then 7 days.

    Returns:
        Formatted info about the next repo to promote, including all
        metadata needed for rendering promotional content.
    """
    rotation = _load_rotation()

    if not rotation.get("repos"):
        return f"{Colors.YELLOW}[!] No repos in rotation. Run scan_git_repos() first.{Colors.RESET}"

    # Resolve cooldown
    if cooldown_days <= 0:
        config = _get_profile_config()
        cooldown_days = config.get("cooldown_days", DEFAULT_COOLDOWN_DAYS)

    now = datetime.now()
    candidates = []

    for repo_name, entry in rotation["repos"].items():
        promoted_at = entry.get("promoted_at")
        repo_data = entry.get("repo_data", {})

        # Skip archived
        if repo_data.get("status") == "archived":
            continue

        # Calculate days since last promotion
        if promoted_at:
            try:
                last_promo = datetime.fromisoformat(promoted_at)
                days_since = (now - last_promo).days
            except (ValueError, TypeError):
                days_since = 999  # Treat malformed dates as long ago
        else:
            days_since = 999  # Never promoted = highest priority

        # Only include if outside cooldown window
        if days_since >= cooldown_days:
            candidates.append((repo_name, days_since, repo_data))

    if not candidates:
        # All repos are in cooldown — pick the one promoted longest ago
        all_entries = [
            (name, entry.get("promoted_at"), entry.get("repo_data", {}))
            for name, entry in rotation["repos"].items()
            if entry.get("repo_data", {}).get("status") != "archived"
        ]
        if not all_entries:
            return f"{Colors.YELLOW}[!] No promotable repos found.{Colors.RESET}"

        # Sort by promoted_at ascending (oldest first, None = never)
        all_entries.sort(key=lambda x: x[1] or "0000")
        repo_name, _, repo_data = all_entries[0]
        candidates = [(repo_name, 999, repo_data)]
        output_note = f"{Colors.YELLOW}⚠️  All repos are in cooldown (>{cooldown_days}d). Selecting the oldest: {repo_name}{Colors.RESET}\n\n"
    else:
        output_note = ""

    # Sort by days_since descending (longest ago first), then by stars descending
    candidates.sort(key=lambda x: (x[1], x[2].get("stars", 0)), reverse=True)

    repo_name, days_since, repo_data = candidates[0]

    # Mark this repo as the current promotion target
    rotation["current_repo"] = repo_name
    _save_rotation(rotation)

    # Format output
    last_promoted = f"{days_since} days ago" if days_since < 999 else "Never"
    output = output_note
    output += f"{Colors.GREEN}▶ Next repo to promote:{Colors.RESET} {Colors.CYAN}{repo_name}{Colors.RESET}\n"
    output += f"  Last promoted: {last_promoted}\n"
    output += f"  Stars: {repo_data.get('stars', 0)} | Forks: {repo_data.get('forks', 0)}\n"
    output += f"  Language: {repo_data.get('language', 'Unknown')}\n"
    output += f"  Topics: {', '.join(repo_data.get('topics', [])) or 'None'}\n"
    output += f"  Repo URL: {repo_data.get('repo_url', '')}\n"
    output += f"  Tagline: {repo_data.get('tagline', '')}\n"
    output += f"  Description: {repo_data.get('description', '')}\n"
    output += f"  Tech Stack: {', '.join(repo_data.get('tech_stack', []))}\n"
    output += f"  Key Features: {'; '.join(repo_data.get('key_features', []))}\n"
    output += f"\n  {Colors.GRAY}Use inject_repo_to_promote() to load this into the promotion engine.{Colors.RESET}"

    logger.info(f"Next repo to promote: {repo_name} (last promoted {last_promoted})")
    return output


def inject_repo_to_promote(repo_name: str = "") -> str:
    """Load the next (or specified) repo's data into the promotion engine.

    Persists the selected repo's metadata to promotion_profile.json (project section)
    so that any new PromotionEngine instance will use the injected data. Also saves
    the original project data under _original_project for restoration.

    Marks the repo as promoted in the rotation file.

    Args:
        repo_name: Specific repo to inject. If empty, uses the current
                   rotation target (set by get_next_repo_to_promote).

    Returns:
        Confirmation message showing what was injected.
    """
    rotation = _load_rotation()

    if not repo_name:
        repo_name = rotation.get("current_repo", "")

    if not repo_name or repo_name not in rotation.get("repos", {}):
        available = list(rotation.get("repos", {}).keys())
        if not available:
            return f"{Colors.RED}[ERROR] No repos in rotation. Run scan_git_repos() first.{Colors.RESET}"
        return f"{Colors.RED}[ERROR] Repo '{repo_name}' not found. Available: {', '.join(available[:10])}{Colors.RESET}"

    entry = rotation["repos"][repo_name]
    repo_data = entry.get("repo_data", {})

    # Persist to promotion_profile.json so new engine instances pick it up
    profile_path = os.path.join(BASE_DIR, "promotion_profile.json")
    try:
        with open(profile_path, 'r', encoding='utf-8') as f:
            profile = json.load(f)

        # Save original project data on first injection (for restoration)
        if "_original_project" not in profile:
            profile["_original_project"] = dict(profile.get("project", {}))

        # Map repo_data fields to profile project section
        field_map = {
            "name": "name",
            "tagline": "tagline",
            "description": "description",
            "tech_stack": "tech_stack",
            "key_features": "key_features",
            "problem_solved": "problem_solved",
            "technical_challenge": "technical_challenge",
            "target_audience": "target_audience",
            "repo_url": "repo_url",
            "good_first_issues": "good_first_issues",
            "status": "status",
        }
        for src_key, dst_key in field_map.items():
            if src_key in repo_data:
                profile["project"][dst_key] = repo_data[src_key]

        # Store scan metadata
        scan_meta_keys = ["stars", "forks", "is_fork", "language", "topics", "readme_excerpt"]
        scan_meta = {k: repo_data[k] for k in scan_meta_keys if k in repo_data}
        if scan_meta:
            profile["project"]["_scan_meta"] = scan_meta

        with open(profile_path, 'w', encoding='utf-8') as f:
            json.dump(profile, f, indent=2, ensure_ascii=False)

        logger.info(f"Persisted repo '{repo_name}' data to promotion_profile.json")
    except Exception as e:
        logger.error(f"Failed to persist repo data to promotion_profile.json: {e}")
        return f"{Colors.RED}[ERROR] Failed to persist repo data: {e}{Colors.RESET}"

    # Also inject into a fresh engine instance (for in-memory use in same process)
    engine = PromotionEngine()
    engine.inject_repo(repo_data)

    # Mark as promoted now
    rotation["repos"][repo_name]["promoted_at"] = datetime.now().isoformat()
    rotation["current_repo"] = repo_name
    _save_rotation(rotation)

    output = f"{Colors.GREEN}✅ Injected repo data into promotion engine:{Colors.RESET}\n"
    output += f"  Project: {repo_data.get('name', repo_name)}\n"
    output += f"  Repo URL: {repo_data.get('repo_url', '')}\n"
    output += f"  Tagline: {repo_data.get('tagline', '')}\n"
    output += f"  Tech Stack: {', '.join(repo_data.get('tech_stack', []))}\n"
    output += f"  {Colors.GRAY}Marked as promoted in rotation file.{Colors.RESET}\n"
    output += f"  {Colors.GRAY}Call render_promotion_post() or use promote.py to generate content.{Colors.RESET}"

    logger.info(f"Injected repo '{repo_name}' into promotion engine and marked as promoted")
    return output


def list_promoted_repos() -> str:
    """List all repos in the rotation with their promotion history.

    Shows every tracked repo, when it was last promoted, and its star count.
    Useful for reviewing the rotation state before generating content.

    Returns:
        Formatted table of all repos in the rotation.
    """
    rotation = _load_rotation()

    if not rotation.get("repos"):
        return f"{Colors.YELLOW}[!] No repos in rotation. Run scan_git_repos() first.{Colors.RESET}"

    output = f"{Colors.CYAN}📋 Git Rotation State{Colors.RESET}\n"
    output += f"  Last scan: {rotation.get('last_scan', 'Never')}\n"
    output += f"  Total scans: {rotation.get('scan_count', 0)}\n\n"

    # Sort by promoted_at (None = never promoted first, then oldest first)
    entries = []
    for name, entry in rotation["repos"].items():
        promoted_at = entry.get("promoted_at")
        repo_data = entry.get("repo_data", {})
        stars = repo_data.get("stars", 0)
        lang = repo_data.get("language", "?")
        entries.append((name, promoted_at, stars, lang))

    entries.sort(key=lambda x: x[1] or "0000")

    output += f"  {'Repo':<30} {'Stars':>5} {'Lang':<12} {'Last Promoted'}\n"
    output += f"  {'─' * 30} {'─' * 5} {'─' * 12} {'─' * 20}\n"

    for name, promoted_at, stars, lang in entries:
        last = promoted_at[:10] if promoted_at else f"{Colors.GRAY}Never{Colors.RESET}"
        marker = f"{Colors.GREEN}★{Colors.RESET}" if stars > 0 else " "
        output += f"  {marker}{name:<29} {stars:>5} {lang:<12} {last}\n"

    output += f"\n  {Colors.GRAY}Total: {len(entries)} repos tracked{Colors.RESET}"

    logger.info("Listed promoted repos from rotation")
    return output


def reset_rotation() -> str:
    """Reset the promotion rotation, clearing all promoted-at timestamps.

    This allows all repos to be promoted again from scratch. The repo
    metadata is preserved — only the promotion history is cleared.

    Returns:
        Confirmation message.
    """
    rotation = _load_rotation()

    count = 0
    for repo_name in rotation.get("repos", {}):
        if rotation["repos"][repo_name].get("promoted_at"):
            rotation["repos"][repo_name]["promoted_at"] = None
            count += 1

    rotation["current_repo"] = None
    _save_rotation(rotation)

    output = f"{Colors.GREEN}✅ Rotation reset: {count} repos cleared{Colors.RESET}\n"
    output += f"  {Colors.GRAY}All repos are now eligible for promotion again.{Colors.RESET}"

    logger.info(f"Reset git rotation: cleared {count} promoted_at entries")
    return output