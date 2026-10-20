#!/usr/bin/env python3
"""Open / update / close GitHub issues based on data/latest.json (stdlib only).

- Site down, no open issue  -> open "🔴 <name> is down" (label: site-down)
- Site down, issue open     -> refresh the issue body with the latest check
                               (an edit, so no extra email every 30 minutes)
- Site up, issue open       -> comment with the recovery details and close it

Needs GITHUB_TOKEN and GITHUB_REPOSITORY (both provided by GitHub Actions).
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

API = os.environ.get("GITHUB_API_URL", "https://api.github.com")
REPO = os.environ.get("GITHUB_REPOSITORY", "")
TOKEN = os.environ.get("GITHUB_TOKEN", "")
LABEL = "site-down"
ROOT = os.path.dirname(os.path.abspath(__file__))
PAGES_URL = os.environ.get("STATUS_PAGE_URL", "")


def gh(method: str, path: str, payload: dict | None = None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        f"{API}{path}",
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "site-status-monitor",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read()
            return json.loads(body) if body else None
    except urllib.error.HTTPError as e:
        if method == "POST" and path.endswith("/labels") and e.code == 422:
            return None  # label already exists
        raise RuntimeError(f"{method} {path} -> {e.code}: {e.read()[:300]!r}") from e


def title_for(name: str) -> str:
    return f"🔴 {name} is down"


def describe(site: dict) -> str:
    status = f"HTTP {site['status']}" if site["status"] else "no response"
    timing = f"{site['ms']} ms" if site["ms"] is not None else "n/a"
    lines = [
        f"| | |",
        f"|---|---|",
        f"| **URL** | {site['url']} |",
        f"| **Result** | {status} |",
        f"| **Response time** | {timing} |",
        f"| **Reason** | {site['error'] or '-'} |",
        f"| **Attempts** | {site['attempts']} |",
        f"| **Checked at** | {site['checked_at']} |",
    ]
    return "\n".join(lines)


def main() -> int:
    if not TOKEN or not REPO:
        print("GITHUB_TOKEN / GITHUB_REPOSITORY not set; skipping issue sync")
        return 0
    with open(os.path.join(ROOT, "data", "latest.json"), encoding="utf-8") as f:
        latest = json.load(f)

    gh("POST", f"/repos/{REPO}/labels",
       {"name": LABEL, "color": "d73a4a", "description": "Opened automatically by the uptime monitor"})

    # oldest first, so the original issue wins if a duplicate ever slipped in
    open_issues = {}
    page = 1
    while True:
        batch = gh("GET", f"/repos/{REPO}/issues?state=open&labels={LABEL}"
                          f"&sort=created&direction=asc&per_page=100&page={page}")
        for issue in batch:
            if "pull_request" in issue:
                continue
            keep = open_issues.setdefault(issue["title"], issue)
            if keep is not issue:  # duplicate (e.g. API read-after-write lag): fold it into the original
                gh("POST", f"/repos/{REPO}/issues/{issue['number']}/comments",
                   {"body": f"Duplicate of #{keep['number']}."})
                gh("PATCH", f"/repos/{REPO}/issues/{issue['number']}",
                   {"state": "closed", "state_reason": "not_planned"})
                print(f"closed duplicate #{issue['number']} of #{keep['number']}")
        if len(batch) < 100:
            break
        page += 1

    footer = f"\n\n_Status page: {PAGES_URL}_" if PAGES_URL else ""
    for site in latest["sites"]:
        title = title_for(site["name"])
        issue = open_issues.get(title)
        if not site["ok"]:
            if issue is None:
                since = site["checked_at"]
                body = (f"**{site['name']}** failed its uptime check (after a retry).\n\n"
                        f"Down since: {since}\n\n### Latest check\n{describe(site)}{footer}\n\n"
                        "<!-- site-status:since=" + since + " -->")
                created = gh("POST", f"/repos/{REPO}/issues", {"title": title, "body": body, "labels": [LABEL]})
                print(f"opened #{created['number']}: {title}")
            else:
                prev = issue.get("body") or ""
                marker = "<!-- site-status:since="
                since = prev.split(marker, 1)[1].split(" -->", 1)[0] if marker in prev else issue["created_at"]
                body = (f"**{site['name']}** is still failing its uptime check.\n\n"
                        f"Down since: {since}\n\n### Latest check\n{describe(site)}{footer}\n\n"
                        f"{marker}{since} -->")
                gh("PATCH", f"/repos/{REPO}/issues/{issue['number']}", {"body": body})
                print(f"updated #{issue['number']}: {title}")
        elif issue is not None:
            timing = f"{site['ms']} ms" if site["ms"] is not None else "n/a"
            gh("POST", f"/repos/{REPO}/issues/{issue['number']}/comments",
               {"body": f"🟢 **{site['name']}** is back up (HTTP {site['status']}, {timing}) at {site['checked_at']}. Closing."})
            gh("PATCH", f"/repos/{REPO}/issues/{issue['number']}", {"state": "closed", "state_reason": "completed"})
            print(f"closed #{issue['number']}: {site['name']} recovered")
    return 0


if __name__ == "__main__":
    sys.exit(main())
