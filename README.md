# PR Check Monitor

Live CI status dashboard, auto-discovering every open PR you (`config.json` → `author`)
have opened in the last `window_hours` across `config.json` → `org`. Served via GitHub
Pages and kept up to date by two local `launchd` jobs on the author's Mac — **the
dashboard depends on the Mac being on and those jobs running.**

(An attempt was made to make `data.json` compute live from GitHub on every request, in a
Vercel Edge Function, independent of the Mac — see git history around "Make PR/check
status live even when the Mac is off" if picking that back up. It was reverted: an
org-wide discovery scan on every request, even cached and chunked, pushed total GitHub
API usage too close to the rate limit, and one bad run silently tripped a secondary
rate-limit lockout that broke every tracked PR — for both the Vercel function *and* the
Mac's own `fetch.py`, since GitHub's primary limit and the report of an exhausted user-ID
lockout aren't neatly per-token. Reverted proxy/api/data.js and events.js to the simple
single-file relay below, which makes at most 1 GitHub call per cache miss.)

## How it works — two speeds, so it stays close to real-time without hitting GitHub's rate limits
- **`fetch.py`** (full discovery, ~every 2 min — the org-wide repo scan is the slow part):
  lists every repo in `org`, finds open PRs authored by `author` created within
  `window_hours`, plus anything pinned in `prs.json`, refreshes full metadata
  (including `reviewDecision`), and rebuilds the tracked-PR list.
- **`fetch.py --fast`** (~every 20s): skips discovery entirely and just re-checks the PRs
  already in the last snapshot — pure REST calls (`gh api .../pulls/{n}` +
  `.../check-runs`), kept out of the GraphQL budget the discovery scan uses, so it can
  run far more often without tripping rate limits. `reviewDecision` is carried forward
  unchanged between full runs.
- Both write `docs/data.json` + diff against the previous snapshot to append state-change
  events (status flips, checks starting/passing/failing, pushes, review decisions) to
  `docs/events.json` (capped at 400) — this powers the Activity feed and the voice/sound
  alerts.
- `docs/index.html` (served by GitHub Pages) reads `data.json`/`events.json` from, in
  order: **1)** `prmon-live-data` — a small Vercel Edge Function (source in `proxy/`,
  deployed separately from this repo, **not** git-linked) that relays this repo's own
  `docs/data.json`/`docs/events.json` via GitHub's Contents API with a short in-memory
  cache (3s with a `GITHUB_TOKEN` env var set on the Vercel project, else 75s to stay
  under the 60/hr unauthenticated limit) — no GitHub CDN in this path, so it's fresher
  than raw.githubusercontent.com, but it's still just relaying whatever the Mac last
  pushed. **2)** `raw.githubusercontent.com` (~5min worst case — its Fastly CDN caches by
  path and ignores cache-busting query params entirely). **3)** the same-origin Pages
  copy. The browser polls every 3s; each tier is only a fallback for when the faster one
  is unreachable.

### Redeploying the live-data proxy
The `prmon-live-data` Vercel project's two files (`proxy/api/data.js`,
`proxy/api/events.js`) aren't git-linked — deploy by pasting the updated file content
into a new deployment targeting the existing project (production, name
`prmon-live-data`), the same way the current one was created. Its `GITHUB_TOKEN` env var
(Settings → Environment Variables) only needs read access to *this* repo for the current
relay-only version.

## Dashboard features
- Stats strip (click any stat to filter), status/submitted filters, free-text search
  (repo, title, branch, check name, #num), repo dropdown, 7 sort modes, group-by-repo.
- Cards view: segmented progress bar, every check as a chip (click → job log) with
  duration / live elapsed timer, branch, diff size, labels, review/mergeable state.
- Table view for dense scanning. "Needs attention" panel lists all failed/running checks.
- Check-duration insights (avg per check name). Dark/light theme. Filters persist.
- **Announcements** (voice, `A`): speaks new activity-feed events aloud as they arrive.
- **Sound alerts** (`L`): a bright chime when a check passes, a ~10s alarm siren when a
  check/PR fails — each poll's new events are checked independently for both.
- Keyboard: `/` search · `1-4` status · `v` view · `t` theme · `e` expand · `g` group ·
  `a` announcements · `l` sound alerts · `r` refresh.
- "copy summary" copies the visible PRs as a markdown table.

## Config
Edit `config.json`:
```json
{ "org": "handshake-project-dynamo", "author": "rajdeepraoextras-dev", "window_hours": 24 }
```
New PRs you open in `org` show up automatically on the next full `fetch.py` run (≤2 min) —
no manual list to maintain. `prs.json` is only for pinning extra PRs outside the
window/org (kept even if `window_hours` doesn't cover their age).

## Managing the background jobs
```bash
# stop / start the fast refresh (known PRs, ~20s)
launchctl unload ~/Library/LaunchAgents/com.rajdeep.prcheckmonitor.fast.plist
launchctl load   ~/Library/LaunchAgents/com.rajdeep.prcheckmonitor.fast.plist

# stop / start the full discovery scan (new PRs, ~2 min)
launchctl unload ~/Library/LaunchAgents/com.rajdeep.prcheckmonitor.plist
launchctl load   ~/Library/LaunchAgents/com.rajdeep.prcheckmonitor.plist

# logs
tail -f launchd.out.log launchd.err.log            # discovery
tail -f launchd.fast.out.log launchd.fast.err.log  # fast refresh
```

## Dashboard URL
https://rajdeepraoextras-dev.github.io/pr-check-monitor/
