# PR Check Monitor

Live CI status dashboard, auto-discovering every open PR you (`config.json` → `author`)
have opened in the last `window_hours` across `config.json` → `org`. Served via GitHub
Pages. PR/check **status is fully live and independent of any local machine** — it's
computed on request by a Vercel Edge Function straight from GitHub's API. Two local
`launchd` jobs on the author's Mac still run, but only to drive the **activity/events log**
(voice/sound alerts, the Activity panel) — see the breakdown below.

## Data sources
- **`docs/index.html` reads `data.json` from `prmon-live-data`** (source in `proxy/`,
  deployed separately from this repo, **not** git-linked — see "Redeploying" below) — a
  Vercel Edge Function that, on request, discovers every open PR by `author` in `org`
  (scans all repos, ~83 currently) and fetches each one's check-runs **directly from
  GitHub's API**, independent of this repo's own `docs/data.json` and independent of
  whether the local Mac is on. Two-tier in-memory cache: which-PRs-exist is cached ~4min
  (the expensive org-wide scan), each PR's check status refreshes every ~25s. Needs a
  `GITHUB_TOKEN` env var (Vercel project settings) with **org-wide read access** — a
  classic PAT with the `repo` scope (a fine-grained token scoped to just this repo is
  NOT enough, since this reads every repo in `org`). Falls back to
  `raw.githubusercontent.com` then the same-origin Pages copy if ever unreachable.
  The browser polls every 3s.
- **`docs/index.html` reads `events.json`** the old way — relayed from this repo's own
  `docs/events.json`, produced by the Mac's `fetch.py` diffing consecutive snapshots.
  This is inherently stateful (a diff against the *previous* run), which a
  stateless-per-request Edge Function can't reconstruct without a real database. So:
  **while the Mac is off, PR/check status stays fully live, but the Activity feed and
  voice/sound/desktop alerts stop getting new entries** (old ones stay visible; nothing
  errors) until the Mac is back and `fetch.py` resumes.
- **`fetch.py`** (full discovery, ~every 2 min) and **`fetch.py --fast`** (~every 20s,
  re-checks known PRs only) still run via `launchd` on the Mac and still write
  `docs/data.json` — now used only as one of `data.json`'s fallback tiers, and as the
  input the events-diffing depends on. See `## Managing the background jobs` below.

### Redeploying the live-data proxy
The `prmon-live-data` Vercel project's two files (`proxy/api/data.js`,
`proxy/api/events.js`) aren't git-linked — deploy by pasting the updated file content
into a new deployment targeting the existing project (production, name
`prmon-live-data`), the same way the current one was created. `data.js` does the live
GitHub discovery+fetch; `events.js` still just relays this repo's `docs/events.json`.
Its `GITHUB_TOKEN` env var (Settings → Environment Variables) needs the `repo` scope on
a classic PAT — **env var changes require a fresh deployment to take effect** (running
instances keep whatever token they started with in `process.env`).

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
