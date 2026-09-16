# PR Check Monitor

Live CI status dashboard, auto-discovering every open PR you (`config.json` → `author`)
have opened in the last `window_hours` across `config.json` → `org`. Served via GitHub
Pages and kept up to date by a local script (`fetch.py`) running every minute via `launchd`.

## How it works
- `fetch.py` lists all repos in `org`, finds open PRs authored by `author` created within
  `window_hours`, plus anything manually pinned in `prs.json`, fetches each PR's check
  status via `gh api .../check-runs`, and writes `docs/data.json`.
- `docs/index.html` (served by GitHub Pages) fetches `docs/data.json` client-side
  and renders the dashboard, refreshing every minute in the browser.
- `launchd` runs `fetch.py`, then commits and pushes `docs/data.json` every 60s.

## Setup (already done for you)
1. `gh repo create ... --public` — created this repo.
2. GitHub Pages enabled, serving from `main` branch `/docs` folder.
3. `launchd` job installed to run `fetch.py` + push every minute.

## Config
Edit `config.json`:
```json
{ "org": "handshake-project-dynamo", "author": "rajdeepraoextras-dev", "window_hours": 24 }
```
New PRs you open in `org` show up automatically on the next `fetch.py` run — no manual
list to maintain. `prs.json` is only for pinning extra PRs outside the window/org (kept
even if `window_hours` doesn't cover their age).

## Managing the background job
```bash
# stop
launchctl unload ~/Library/LaunchAgents/com.rajdeep.prcheckmonitor.plist

# start
launchctl load ~/Library/LaunchAgents/com.rajdeep.prcheckmonitor.plist

# logs
tail -f launchd.out.log launchd.err.log
```

## Dashboard URL
https://rajdeepraoextras-dev.github.io/pr-check-monitor/
