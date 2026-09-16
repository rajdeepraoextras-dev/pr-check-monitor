# PR Check Monitor

Live CI status dashboard for the PRs listed in `prs.json`, served via GitHub Pages
and kept up to date by a local script (`fetch.py`) running every minute via `launchd`.

## How it works
- `fetch.py` runs `gh pr view` / `gh api .../check-runs` for each PR in `prs.json`
  and writes `docs/data.json`.
- `docs/index.html` (served by GitHub Pages) fetches `docs/data.json` client-side
  and renders the dashboard, refreshing every minute in the browser.
- `launchd` runs `fetch.py`, then commits and pushes `docs/data.json` every 60s.

## Setup (already done for you)
1. `gh repo create ... --public` — created this repo.
2. GitHub Pages enabled, serving from `main` branch `/docs` folder.
3. `launchd` job installed to run `fetch.py` + push every minute.

## Editing the tracked PRs
Edit `prs.json` (repo + PR number pairs), commit, push. Next `fetch.py` run picks it up.

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
