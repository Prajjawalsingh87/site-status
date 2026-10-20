#!/usr/bin/env python3
"""Uptime checker (stdlib only).

Reads sites.json, checks every site concurrently, writes data/latest.json and
appends one compact line per run to data/history.jsonl (trimmed to the last
HISTORY_DAYS days).

Exit code is always 0 so the workflow can still commit data and open issues;
use the "down" list in data/latest.json to see what failed.
"""
from __future__ import annotations

import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))
SITES_FILE = os.path.join(ROOT, "sites.json")
DATA_DIR = os.path.join(ROOT, "data")
LATEST_FILE = os.path.join(DATA_DIR, "latest.json")
HISTORY_FILE = os.path.join(DATA_DIR, "history.jsonl")

HISTORY_DAYS = 30
DEFAULT_TIMEOUT = 20  # seconds
DEFAULT_RETRY_DELAY = 5  # seconds
SLOW_MS = 5000  # mark "degraded" when slower than this (still counts as up); per-site "slow_ms" overrides
MAX_BODY = 512 * 1024
USER_AGENT = "site-status-monitor/1.0 (+https://github.com/Prajjawalsingh87/site-status)"


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def lookup(obj, dotted: str):
    """Return obj['a']['b'] for 'a.b'; raises KeyError if missing."""
    cur = obj
    for part in dotted.split("."):
        if isinstance(cur, list) and part.isdigit():
            cur = cur[int(part)]
        elif isinstance(cur, dict):
            cur = cur[part]
        else:
            raise KeyError(dotted)
    return cur


def evaluate(expect: dict, status: int, body: bytes) -> str | None:
    """Return None if expectations pass, else a human-readable reason."""
    want_status = expect.get("status")
    if want_status is not None:
        allowed = want_status if isinstance(want_status, list) else [want_status]
        if status not in allowed:
            return f"expected HTTP {'/'.join(map(str, allowed))}, got {status}"
    elif not 200 <= status < 400:
        return f"HTTP {status}"

    text = body.decode("utf-8", errors="replace")
    if "text" in expect and expect["text"] not in text:
        return f"response does not contain {expect['text']!r}"

    if "json" in expect:
        try:
            doc = json.loads(text)
        except ValueError:
            return "response is not valid JSON"
        for key, want in expect["json"].items():
            try:
                got = lookup(doc, key)
            except (KeyError, IndexError, TypeError):
                return f"JSON field {key!r} missing"
            if got != want:
                return f"JSON {key}={got!r}, expected {want!r}"
    return None


def fetch(url: str, timeout: float):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    ctx = ssl.create_default_context()
    start = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            body = resp.read(MAX_BODY)
            status = resp.status
    except urllib.error.HTTPError as e:  # non-2xx still gives us a status + body
        body = e.read(MAX_BODY) if e.fp else b""
        status = e.code
    ms = int((time.monotonic() - start) * 1000)
    return status, ms, body


def check_once(site: dict):
    timeout = site.get("timeout", DEFAULT_TIMEOUT)
    try:
        status, ms, body = fetch(site["url"], timeout)
    except Exception as e:  # timeouts, DNS, TLS, connection reset ...
        reason = getattr(e, "reason", None) or e
        return {"ok": False, "status": None, "ms": None, "error": f"{type(e).__name__}: {reason}"[:300]}
    err = evaluate(site.get("expect") or {}, status, body)
    return {"ok": err is None, "status": status, "ms": ms, "error": err}


def check_site(site: dict) -> dict:
    attempts = 1
    res = check_once(site)
    if not res["ok"]:
        time.sleep(site.get("retry_delay", DEFAULT_RETRY_DELAY))
        attempts = 2
        res = check_once(site)
    state = "down"
    if res["ok"]:
        state = "degraded" if (res["ms"] or 0) > site.get("slow_ms", SLOW_MS) else "up"
    return {
        "name": site["name"],
        "url": site["url"],
        "state": state,
        "ok": res["ok"],
        "status": res["status"],
        "ms": res["ms"],
        "error": res["error"],
        "attempts": attempts,
        "checked_at": now_iso(),
    }


def trim_history(lines: list[str]) -> list[str]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=HISTORY_DAYS)
    kept = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            t = datetime.fromisoformat(json.loads(line)["t"].replace("Z", "+00:00"))
        except (ValueError, KeyError):
            continue
        if t >= cutoff:
            kept.append(line)
    return kept


def main() -> int:
    with open(SITES_FILE, encoding="utf-8") as f:
        sites = json.load(f)
    names = [s["name"] for s in sites]
    if len(names) != len(set(names)):
        print("sites.json: site names must be unique", file=sys.stderr)
        return 2

    run_at = now_iso()
    with ThreadPoolExecutor(max_workers=min(16, len(sites))) as pool:
        results = list(pool.map(check_site, sites))

    down = [r["name"] for r in results if not r["ok"]]
    latest = {
        "generated_at": run_at,
        "overall": "down" if down else ("degraded" if any(r["state"] == "degraded" for r in results) else "up"),
        "down": down,
        "sites": results,
    }
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(LATEST_FILE, "w", encoding="utf-8") as f:
        json.dump(latest, f, indent=2, ensure_ascii=False)
        f.write("\n")

    # history line: {"t": ts, "r": {name: [ok 0/1, ms|null, status|null]}}
    entry = {"t": run_at, "r": {r["name"]: [1 if r["ok"] else 0, r["ms"], r["status"]] for r in results}}
    lines = []
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, encoding="utf-8") as f:
            lines = f.readlines()
    lines = trim_history(lines)
    lines.append(json.dumps(entry, separators=(",", ":"), ensure_ascii=False))
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    width = max(len(n) for n in names)
    for r in results:
        detail = f"HTTP {r['status']}" if r["status"] else "no response"
        timing = f"{r['ms']} ms" if r["ms"] is not None else "-"
        extra = f"  ({r['error']})" if r["error"] else ""
        print(f"{r['state'].upper():9} {r['name']:<{width}}  {detail:<12} {timing:>8}{extra}")
    print(f"\n{len(results) - len(down)}/{len(results)} up" + (f"; DOWN: {', '.join(down)}" if down else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
