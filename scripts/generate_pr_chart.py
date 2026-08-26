#!/usr/bin/env python3
"""
Fetches the author's pull request stats (open / merged / closed-unmerged)
across all public repos via the GitHub Search API and renders a donut/pie
chart as a standalone SVG file.

Env vars:
  GH_USERNAME   - GitHub username to query (required)
  GH_TOKEN      - token for API auth, raises rate limit from 10/min to 30/min
                  (the workflow passes the built-in GITHUB_TOKEN)
  OUTPUT_PATH   - where to write the SVG (default: assets/pr-chart.svg)
"""

import json
import math
import os
import sys
import urllib.request

GH_USERNAME = os.environ.get("GH_USERNAME")
GH_TOKEN = os.environ.get("GH_TOKEN")
OUTPUT_PATH = os.environ.get("OUTPUT_PATH", "assets/pr-chart.svg")

if not GH_USERNAME:
    print("GH_USERNAME env var is required", file=sys.stderr)
    sys.exit(1)


def gh_search_count(query: str) -> int:
    """Return total_count from the GitHub search/issues endpoint for a query."""
    url = "https://api.github.com/search/issues?q=" + urllib.request.quote(query) + "&per_page=1"
    req = urllib.request.Request(url)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", f"{GH_USERNAME}-pr-chart-action")
    if GH_TOKEN:
        req.add_header("Authorization", f"Bearer {GH_TOKEN}")
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read().decode())
    return data.get("total_count", 0)


def fetch_pr_stats(username: str):
    base = f"author:{username} type:pr"
    total = gh_search_count(base)
    merged = gh_search_count(f"{base} is:merged")
    open_ = gh_search_count(f"{base} is:open")
    closed_unmerged = max(total - merged - open_, 0)
    return {"open": open_, "merged": merged, "closed_unmerged": closed_unmerged, "total": total}


def describe_arc(cx, cy, r, start_angle, end_angle):
    def point(angle_deg):
        angle_rad = math.radians(angle_deg - 90)
        return (cx + r * math.cos(angle_rad), cy + r * math.sin(angle_rad))

    x1, y1 = point(start_angle)
    x2, y2 = point(end_angle)
    large_arc = 1 if (end_angle - start_angle) > 180 else 0
    return f"M {cx},{cy} L {x1:.3f},{y1:.3f} A {r},{r} 0 {large_arc} 1 {x2:.3f},{y2:.3f} Z"


def build_svg(stats: dict, username: str) -> str:
    slices = [
        ("Merged", stats["merged"], "#3fb950"),
        ("Open", stats["open"], "#58a6ff"),
        ("Closed (unmerged)", stats["closed_unmerged"], "#f85149"),
    ]
    total = stats["total"] or 1  # avoid div-by-zero when a user has 0 PRs

    cx, cy, r = 130, 150, 90
    start = 0
    paths = []
    for label, value, color in slices:
        if value == 0:
            continue
        sweep = (value / total) * 360
        end = start + sweep
        paths.append(f'<path d="{describe_arc(cx, cy, r, start, end)}" fill="{color}" stroke="#0d1117" stroke-width="2"/>')
        start = end

    legend_y = 40
    legend_items = []
    for label, value, color in slices:
        pct = (value / total) * 100
        legend_items.append(f'''
    <rect x="270" y="{legend_y - 12}" width="14" height="14" rx="3" fill="{color}"/>
    <text x="292" y="{legend_y}" font-family="Segoe UI, Helvetica, Arial, sans-serif" font-size="14" fill="#c9d1d9">{label}: {value} ({pct:.0f}%)</text>''')
        legend_y += 28

    svg = f'''<svg width="520" height="300" viewBox="0 0 520 300" xmlns="http://www.w3.org/2000/svg">
  <rect width="520" height="300" rx="12" fill="#0d1117"/>
  <text x="20" y="32" font-family="Segoe UI, Helvetica, Arial, sans-serif" font-size="18" font-weight="600" fill="#c9d1d9">{username}'s Pull Request Breakdown</text>
  <g>
    {''.join(paths)}
    <circle cx="{cx}" cy="{cy}" r="45" fill="#0d1117"/>
    <text x="{cx}" y="{cy - 4}" text-anchor="middle" font-family="Segoe UI, Helvetica, Arial, sans-serif" font-size="26" font-weight="700" fill="#c9d1d9">{total}</text>
    <text x="{cx}" y="{cy + 16}" text-anchor="middle" font-family="Segoe UI, Helvetica, Arial, sans-serif" font-size="12" fill="#8b949e">Total PRs</text>
  </g>
  <g>{''.join(legend_items)}</g>
  <text x="20" y="285" font-family="Segoe UI, Helvetica, Arial, sans-serif" font-size="10" fill="#484f58">Auto-updated via GitHub Actions</text>
</svg>'''
    return svg


def gh_graphql(query: str) -> dict:
    body = json.dumps({"query": query}).encode()
    req = urllib.request.Request("https://api.github.com/graphql", data=body, method="POST")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", f"{GH_USERNAME}-pr-chart-action")
    if GH_TOKEN:
        req.add_header("Authorization", f"Bearer {GH_TOKEN}")
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode())


def fetch_profile_stats(username: str) -> dict:
    query = f'''
    {{
      user(login: "{username}") {{
        followers {{ totalCount }}
        contributionsCollection {{ totalCommitContributions restrictedContributionsCount }}
        repositories(first: 100, ownerAffiliations: OWNER, isFork: false) {{
          nodes {{ stargazerCount }}
        }}
      }}
    }}'''
    data = gh_graphql(query)
    user = data.get("data", {}).get("user") or {}
    stars = sum(r["stargazerCount"] for r in user.get("repositories", {}).get("nodes", []))
    cc = user.get("contributionsCollection", {})
    commits = cc.get("totalCommitContributions", 0) + cc.get("restrictedContributionsCount", 0)
    followers = user.get("followers", {}).get("totalCount", 0)
    return {"stars": stars, "commits": commits, "followers": followers}


def build_stats_card_svg(profile: dict, pr_stats: dict, username: str) -> str:
    rows = [
        ("Total Stars", profile["stars"]),
        ("Total Commits", profile["commits"]),
        ("Total PRs", pr_stats["total"]),
        ("PRs Merged", f'{pr_stats["merged"]} ({(pr_stats["merged"]/max(pr_stats["total"],1))*100:.0f}%)'),
        ("Followers", profile["followers"]),
    ]
    row_h = 32
    height = 60 + row_h * len(rows)
    items = []
    y = 60
    for label, value in rows:
        items.append(f'''
    <text x="24" y="{y}" font-family="Segoe UI, Helvetica, Arial, sans-serif" font-size="14" fill="#8b949e">{label}</text>
    <text x="380" y="{y}" text-anchor="end" font-family="Segoe UI, Helvetica, Arial, sans-serif" font-size="14" font-weight="600" fill="#c9d1d9">{value}</text>''')
        y += row_h

    svg = f'''<svg width="400" height="{height}" viewBox="0 0 400 {height}" xmlns="http://www.w3.org/2000/svg">
  <rect width="400" height="{height}" rx="12" fill="#0d1117" stroke="#30363d"/>
  <text x="24" y="32" font-family="Segoe UI, Helvetica, Arial, sans-serif" font-size="16" font-weight="600" fill="#c9d1d9">{username}'s GitHub Stats</text>
  {''.join(items)}
</svg>'''
    return svg


def main():
    pr_stats = fetch_pr_stats(GH_USERNAME)
    pie_svg = build_svg(pr_stats, GH_USERNAME)
    os.makedirs(os.path.dirname(OUTPUT_PATH) or ".", exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        f.write(pie_svg)
    print(f"Wrote {OUTPUT_PATH}: {pr_stats}")

    try:
        profile = fetch_profile_stats(GH_USERNAME)
        stats_path = os.environ.get("STATS_OUTPUT_PATH", "assets/stats-card.svg")
        with open(stats_path, "w") as f:
            f.write(build_stats_card_svg(profile, pr_stats, GH_USERNAME))
        print(f"Wrote {stats_path}: {profile}")
    except Exception as e:
        print(f"Skipping stats card (GraphQL fetch failed): {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
