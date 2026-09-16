# PR Check Monitor

Live CI status dashboard, auto-discovering every open PR you (`config.json` → `author`)
have opened in the last `window_hours` across `config.json` → `org`. Served via GitHub
Pages and kept up to date by two local `launchd` jobs.

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
  deployed separately from this repo) that proxies GitHub's Contents API with its own
  in-memory cache; with a `GITHUB_TOKEN` env var set on that Vercel project it caches for
  only 3s (GitHub's authenticated budget is 5000/hr), otherwise 75s (to stay under the
  60/hr unauthenticated limit). No GitHub CDN in this path at all, so this is the
  freshest source. **2)** `raw.githubusercontent.com` (~5min worst case — its Fastly CDN
  caches by path and ignores cache-busting query params entirely). **3)** the same-origin
  Pages copy. The browser polls every 3s; each tier is only a fallback for when the
  faster one is unreachable.

### Redeploying the live-data proxy
The `prmon-live-data` Vercel project's two files (`proxy/api/data.js`,
`proxy/api/events.js`) aren't git-linked — they were deployed directly via `vercel deploy`
(or the Vercel dashboard) targeting production, project name `prmon-live-data`. Its
`GITHUB_TOKEN` env var (Settings → Environment Variables) is a fine-grained PAT scoped to
read-only Contents access on this one repo. Redeploy after editing either file by running
a Vercel deploy from the `proxy/` directory, or pasting the updated file content into a
new deployment the same way this one was created.

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
