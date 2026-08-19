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
        "border": "#d0d7de",
        "title": "#1f2328",
        "label": "#636c76",
        "value": "#1f2328",
        "accent": "#0969da",
        "divider": "#d8dee4",
    },
    "dark": {
        "background": "#0d1117",
        "border": "#30363d",
        "title": "#f0f6fc",
        "label": "#8b949e",
        "value": "#f0f6fc",
        "accent": "#58a6ff",
        "divider": "#21262d",
    },
}


def format_number(value: int) -> str:
    return f"{value:,}"


def render_svg(username: str, stats: Stats, theme_name: str) -> str:
    theme = THEMES[theme_name]
    metrics = [
        ("Total Contributions", stats.total_contributions),
        ("Commit Contributions", stats.commit_contributions),
        ("Private Contributions", stats.private_contributions),
        ("Pull Requests", stats.pull_requests),
        ("Issues", stats.issues),
        ("Stars Earned", stats.stars_earned),
    ]

    cells = []
    for index, (label, value) in enumerate(metrics):
        column = index % 3
        row = index // 3
        x = 36 + column * 228
        y = 105 + row * 92
        cells.append(
            f"""  <g transform="translate({x} {y})">
    <text class="value" x="0" y="0">{format_number(value)}</text>
    <text class="label" x="0" y="27">{escape(label)}</text>
  </g>"""
        )

    cells_svg = "\n".join(cells)

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="720" height="238"
  viewBox="0 0 720 238" role="img" aria-labelledby="title description">
  <title id="title">{escape(username)} GitHub activity</title>
  <desc id="description">
    Lifetime GitHub contribution statistics with private contributions shown separately.
  </desc>
  <style>
    text {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; }}
    .heading {{ fill: {theme['title']}; font-size: 18px; font-weight: 600; }}
    .eyebrow {{ fill: {theme['accent']}; font-size: 12px; font-weight: 600; letter-spacing: 0.08em; }}
    .value {{
      fill: {theme['value']};
      font-size: 25px;
      font-weight: 600;
      font-variant-numeric: tabular-nums;
    }}
    .label {{ fill: {theme['label']}; font-size: 13px; font-weight: 400; }}
  </style>
  <rect x="0.5" y="0.5" width="719" height="237" rx="10"
    fill="{theme['background']}" stroke="{theme['border']}"/>
  <text class="eyebrow" x="36" y="34">GITHUB ACTIVITY</text>
  <text class="heading" x="36" y="61">Lifetime contribution summary</text>
  <line x1="36" y1="77.5" x2="684" y2="77.5" stroke="{theme['divider']}"/>
  <line x1="246" y1="91" x2="246" y2="211" stroke="{theme['divider']}"/>
  <line x1="474" y1="91" x2="474" y2="211" stroke="{theme['divider']}"/>
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
