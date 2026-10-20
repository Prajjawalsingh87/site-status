# site-status

Uptime monitor and public status page for Prajjawal Singh's live sites and APIs.

**Status page:** https://prajjawalsingh87.github.io/site-status/

## How it works

1. **Schedule.** `.github/workflows/check.yml` runs every 30 minutes (and on demand through *Run workflow*).
2. **Check.** `check.py` (Python standard library only) reads `sites.json` and requests every URL in parallel. For each one it records the HTTP status, the response time, and whether the optional expectation held. A failed check is retried once before the site counts as **down**. A site that answers correctly but takes longer than 5 s counts as **slow**, which still counts as up.
3. **Store.** The results go to `data/latest.json`, and one compact line per run is appended to `data/history.jsonl`. History older than 30 days is trimmed on every run. The workflow commits both files back to `main`.
4. **Alert.** `notify.py` syncs GitHub issues using the built-in `GITHUB_TOKEN`, which only has `contents: write` and `issues: write`:
   - When a site goes down, it opens an issue titled `🔴 <name> is down` with the label `site-down`. GitHub then emails the repo owner.
   - While the site stays down, it edits the same issue's details instead of opening a duplicate or posting a comment every 30 minutes.
   - When the site recovers, it comments with the recovery details and closes the issue, which sends another email.
5. **Display.** `index.html` is a static page served by GitHub Pages from `main`. It reads `data/latest.json` and `data/history.jsonl` and shows:
   - an overall banner
   - current status and response time for each site
   - 30-day uptime %
   - a bar for each of the last 30 days: green = 100%, amber ≥ 95%, red < 95%, grey = no data

## Adding a site

Add an entry to `sites.json`:

```json
{ "name": "My App", "url": "https://my-app.vercel.app" }
```

Optional fields:

| field | meaning |
|---|---|
| `expect.status` | Required HTTP status, or a list of allowed statuses. If omitted, any 2xx/3xx passes. |
| `expect.text` | A substring the response body must contain. |
| `expect.json` | `{ "field": value }` pairs the JSON body must match. Dotted paths such as `"db.status"` work. |
| `timeout` | Request timeout in seconds (default 20). Raise it for hosts that cold-start. |
| `retry_delay` | Seconds to wait before the single retry (default 5). |
| `slow_ms` | Response time above which the site shows as "Slow" (default 5000). |

Example with every option, for a Render free-tier service that sleeps:

```json
{
  "name": "File Agent (Render)",
  "url": "https://file-agent.onrender.com/health",
  "expect": { "status": 200, "json": { "status": "ok" } },
  "timeout": 90,
  "retry_delay": 20
}
```

Each name must be unique. The name appears in the issue title and keys the history.

## Running locally

```sh
python3 check.py                     # prints a table and writes data/
python3 -m http.server 8000          # then open http://localhost:8000
```

`notify.py` does nothing unless `GITHUB_TOKEN` and `GITHUB_REPOSITORY` are set.
