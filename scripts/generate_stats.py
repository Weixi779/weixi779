#!/usr/bin/env python3

# Created by weixi on 2026/08/20.

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from html import escape
from pathlib import Path


GITHUB_GRAPHQL_URL = "https://api.github.com/graphql"


@dataclass(frozen=True)
class Stats:
    total_contributions: int
    commit_contributions: int
    private_contributions: int
    pull_requests: int
    issues: int
    stars_earned: int


def github_token() -> str:
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        return token

    result = subprocess.run(
        ["gh", "auth", "token"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()

    raise RuntimeError("GITHUB_TOKEN is not set and no GitHub CLI token is available")


def graphql(token: str, query: str, variables: dict[str, object]) -> dict[str, object]:
    request = urllib.request.Request(
        GITHUB_GRAPHQL_URL,
        data=json.dumps({"query": query, "variables": variables}).encode("utf-8"),
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "weixi779-profile-stats",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub GraphQL request failed ({error.code}): {detail}") from error

    if payload.get("errors"):
        messages = "; ".join(error.get("message", "Unknown error") for error in payload["errors"])
        raise RuntimeError(f"GitHub GraphQL returned errors: {messages}")

    return payload["data"]


def fetch_profile(token: str, username: str) -> tuple[list[int], int]:
    query = """
    query Profile($login: String!, $cursor: String) {
      user(login: $login) {
        contributionsCollection {
          contributionYears
        }
        repositories(
          first: 100
          after: $cursor
          ownerAffiliations: OWNER
          privacy: PUBLIC
          isFork: false
        ) {
          nodes {
            stargazerCount
          }
          pageInfo {
            hasNextPage
            endCursor
          }
        }
      }
    }
    """

    years: list[int] = []
    stars = 0
    cursor: str | None = None

    while True:
        data = graphql(token, query, {"login": username, "cursor": cursor})
        user = data.get("user")
        if not user:
            raise RuntimeError(f"GitHub user not found: {username}")

        if not years:
            years = sorted(set(user["contributionsCollection"]["contributionYears"]))

        repositories = user["repositories"]
        stars += sum(repository["stargazerCount"] for repository in repositories["nodes"])
        page_info = repositories["pageInfo"]
        if not page_info["hasNextPage"]:
            break
        cursor = page_info["endCursor"]

    return years, stars


def fetch_contributions(token: str, username: str, years: list[int]) -> Stats:
    if not years:
        return Stats(0, 0, 0, 0, 0, 0)

    fields = []
    for year in years:
        fields.append(
            f"""
            year{year}: contributionsCollection(
              from: \"{year}-01-01T00:00:00Z\"
              to: \"{year}-12-31T23:59:59Z\"
            ) {{
              contributionCalendar {{
                totalContributions
              }}
              totalCommitContributions
              totalPullRequestContributions
              totalIssueContributions
              restrictedContributionsCount
            }}
            """
        )

    query = f"""
    query Contributions($login: String!) {{
      user(login: $login) {{
        {''.join(fields)}
      }}
    }}
    """
    data = graphql(token, query, {"login": username})
    user = data.get("user")
    if not user:
        raise RuntimeError(f"GitHub user not found: {username}")

    collections = user.values()
    return Stats(
        total_contributions=sum(item["contributionCalendar"]["totalContributions"] for item in collections),
        commit_contributions=sum(item["totalCommitContributions"] for item in collections),
        private_contributions=sum(item["restrictedContributionsCount"] for item in collections),
        pull_requests=sum(item["totalPullRequestContributions"] for item in collections),
        issues=sum(item["totalIssueContributions"] for item in collections),
        stars_earned=0,
    )


def fetch_stats(token: str, username: str) -> Stats:
    years, stars = fetch_profile(token, username)
    contributions = fetch_contributions(token, username, years)
    return Stats(
        total_contributions=contributions.total_contributions,
        commit_contributions=contributions.commit_contributions,
        private_contributions=contributions.private_contributions,
        pull_requests=contributions.pull_requests,
        issues=contributions.issues,
        stars_earned=stars,
    )


THEMES = {
    "light": {
        "background": "#ffffff",
        "border": "#e7e9ec",
        "title": "#181a1f",
        "label": "#70757f",
        "value": "#202228",
        "accent": "#f05138",
        "divider": "#eceef1",
    },
    "dark": {
        "background": "#0d0f12",
        "border": "#262a30",
        "title": "#f3f4f6",
        "label": "#979ca6",
        "value": "#f3f4f6",
        "accent": "#ff7657",
        "divider": "#24272d",
    },
}


ICONS = {
    "activity": """
      <path d="M1 8h3l2.2-4.5 3.3 9 2.1-4.5H15"/>
    """,
    "commit": """
      <path d="M1 8h4M11 8h4"/>
      <circle cx="8" cy="8" r="3"/>
    """,
    "lock": """
      <rect x="3" y="7" width="10" height="7" rx="2"/>
      <path d="M5.5 7V5a2.5 2.5 0 0 1 5 0v2"/>
    """,
    "pull_request": """
      <circle cx="4" cy="3" r="2"/>
      <circle cx="12" cy="13" r="2"/>
      <path d="M4 5v8M10 3h1a3 3 0 0 1 3 3v2M10 1l2 2-2 2"/>
    """,
    "issue": """
      <circle cx="8" cy="8" r="6"/>
      <path d="M8 4.5v4M8 11.5h.01"/>
    """,
    "star": """
      <path d="m8 1.5 2 4.1 4.5.7-3.3 3.2.8 4.5-4-2.1L4 14l.8-4.5-3.3-3.2 4.5-.7Z"/>
    """,
}


def format_number(value: int) -> str:
    return f"{value:,}"


def render_svg(username: str, stats: Stats, theme_name: str) -> str:
    theme = THEMES[theme_name]
    metrics = [
        ("Total Contributions", stats.total_contributions, "activity"),
        ("Commit Contributions", stats.commit_contributions, "commit"),
        ("Private Contributions", stats.private_contributions, "lock"),
        ("Pull Requests", stats.pull_requests, "pull_request"),
        ("Issues", stats.issues, "issue"),
        ("Stars Earned", stats.stars_earned, "star"),
    ]

    cells = []
    for index, (label, value, icon) in enumerate(metrics):
        column = index // 3
        row = index % 3
        x = 32 + column * 360
        y = 76 + row * 56
        delay = index * 65
        cells.append(
            f"""  <g transform="translate({x} {y})">
    <g class="icon" transform="translate(0 6)">
{ICONS[icon].rstrip()}
    </g>
    <text class="metric-label" x="30" y="13">{escape(label)}</text>
    <text class="metric-value" x="30" y="40" style="--delay: {delay}ms">{format_number(value)}</text>
  </g>"""
        )

    cells_svg = "\n".join(cells)

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="720" height="254"
  viewBox="0 0 720 254" role="img" aria-labelledby="title description">
  <title id="title">{escape(username)} GitHub activity</title>
  <desc id="description">
    Lifetime GitHub contribution statistics with private contributions shown separately.
  </desc>
  <style>
    text {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; }}
    .heading {{ fill: {theme['title']}; font-size: 17px; font-weight: 600; }}
    .subtitle {{ fill: {theme['label']}; font-size: 12px; }}
    .icon {{
      fill: none;
      stroke: {theme['accent']};
      stroke-linecap: round;
      stroke-linejoin: round;
      stroke-width: 1.5;
    }}
    .metric-value {{
      fill: {theme['value']};
      font-size: 21px;
      font-weight: 600;
      font-variant-numeric: tabular-nums;
      opacity: 1;
      animation: reveal 420ms cubic-bezier(0.22, 1, 0.36, 1) both;
      animation-delay: var(--delay);
    }}
    .metric-label {{ fill: {theme['label']}; font-size: 12px; }}
    @keyframes reveal {{
      from {{ opacity: 0; transform: translateY(4px); }}
      to {{ opacity: 1; transform: translateY(0); }}
    }}
    @media (prefers-reduced-motion: reduce) {{
      .metric-value {{ opacity: 1; animation: none; }}
    }}
  </style>
  <rect x="0.5" y="0.5" width="719" height="253" rx="18"
    fill="{theme['background']}" stroke="{theme['border']}"/>
  <rect x="32" y="22" width="28" height="28" rx="9" fill="{theme['accent']}"/>
  <path d="M38 36h3l1.8-3.5 2.6 7 1.8-3.5H54" fill="none" stroke="white"
    stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>
  <text class="heading" x="68" y="34">GitHub Activity</text>
  <text class="subtitle" x="68" y="52">Lifetime contribution summary</text>
  <line x1="360" y1="76" x2="360" y2="236" stroke="{theme['divider']}"/>
  <line x1="32" y1="132" x2="328" y2="132" stroke="{theme['divider']}"/>
  <line x1="32" y1="188" x2="328" y2="188" stroke="{theme['divider']}"/>
  <line x1="392" y1="132" x2="688" y2="132" stroke="{theme['divider']}"/>
  <line x1="392" y1="188" x2="688" y2="188" stroke="{theme['divider']}"/>
{cells_svg}
</svg>
"""


def write_svgs(output_directory: Path, username: str, stats: Stats) -> None:
    output_directory.mkdir(parents=True, exist_ok=True)
    for theme_name in THEMES:
        path = output_directory / f"stats-{theme_name}.svg"
        path.write_text(render_svg(username, stats, theme_name), encoding="utf-8")
        print(f"Wrote {path}")


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate GitHub profile statistics SVGs")
    parser.add_argument("--username", required=True, help="GitHub username")
    parser.add_argument("--output-dir", type=Path, default=Path("profile"))
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    try:
        stats = fetch_stats(github_token(), arguments.username)
        write_svgs(arguments.output_dir, arguments.username, stats)
    except (RuntimeError, urllib.error.URLError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
