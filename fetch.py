#!/usr/bin/env python3
"""
Discover open PRs authored by config.author in config.org opened within
config.window_hours, fetch their check status, and write docs/data.json
(for GitHub Pages).

Any PRs listed in prs.json are always included too (manual pins), regardless
of age.
"""
import json, subprocess, datetime, os

ROOT = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(ROOT, "config.json")
PRS_FILE = os.path.join(ROOT, "prs.json")
OUT_FILE = os.path.join(ROOT, "docs", "data.json")


def run(cmd):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    return r.stdout


def discover_prs(org, author, window_hours):
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=window_hours)
    repos = run(f'gh repo list {org} --limit 200 --json name --jq ".[].name"').strip().splitlines()
    found = []
    for repo_name in repos:
        repo = f"{org}/{repo_name}"
        raw = run(
            f'gh pr list -R {repo} --author {author} --state open '
            f'--json number,createdAt --jq "."'
        )
        try:
            prs = json.loads(raw) if raw.strip() else []
        except Exception:
            prs = []
        for p in prs:
            created = datetime.datetime.fromisoformat(p["createdAt"].replace("Z", "+00:00"))
            if created >= cutoff:
                found.append({"repo": repo, "num": str(p["number"])})
    return found


def main():
    config = json.load(open(CONFIG_FILE)) if os.path.exists(CONFIG_FILE) else {}
    org = config.get("org")
    author = config.get("author")
    window_hours = config.get("window_hours", 24)

    prs = []
    if org and author:
        prs = discover_prs(org, author, window_hours)

    pinned = json.load(open(PRS_FILE)) if os.path.exists(PRS_FILE) else []
    seen = {(p["repo"], p["num"]) for p in prs}
    for p in pinned:
        if (p["repo"], p["num"]) not in seen:
            prs.append(p)
            seen.add((p["repo"], p["num"]))

    prev_by_key = {}
    if os.path.exists(OUT_FILE):
        try:
            for p in json.load(open(OUT_FILE)).get("prs", []):
                prev_by_key[p["repo"] + p["num"]] = p
        except Exception:
            pass

    out = []
    for entry in prs:
        repo, num = entry["repo"], entry["num"]
        try:
            info = json.loads(run(f'gh pr view {num} -R {repo} --json headRefOid,title,url,createdAt'))
            sha = info["headRefOid"]
            raw = run(f'gh api "repos/{repo}/commits/{sha}/check-runs" --paginate')
            checkruns = []
            for line in raw.strip().splitlines():
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                    checkruns.extend(data.get("check_runs", []))
                except Exception:
                    pass

            passed = sum(1 for c in checkruns if c.get("conclusion") == "success")
            failed = [c["name"].replace("review / ", "") for c in checkruns if c.get("conclusion") == "failure"]
            running = [
                {"name": c["name"].replace("review / ", ""), "started_at": c.get("started_at")}
                for c in checkruns if c.get("status") == "in_progress"
            ]
            total = len(checkruns)
            overall = "failing" if failed else ("running" if running else "passed")

            key = repo.split("/")[-1] + num
            submitted = prev_by_key.get(key, {}).get("submitted", False)

            out.append({
                "repo": repo.split("/")[-1],
                "num": num,
                "title": info["title"],
                "url": info["url"],
                "createdAt": info.get("createdAt"),
                "overall": overall,
                "passed": passed,
                "total": total,
                "failed": failed,
                "running": running,
                "submitted": submitted,
            })
        except Exception as e:
            key = repo.split("/")[-1] + num
            out.append({
                "repo": repo.split("/")[-1], "num": num, "title": "(error fetching)",
                "url": f"https://github.com/{repo}/pull/{num}",
                "overall": "error", "passed": 0, "total": 0, "failed": [str(e)], "running": [],
                "submitted": prev_by_key.get(key, {}).get("submitted", False),
            })

    # newest first
    out.sort(key=lambda p: p.get("createdAt") or "", reverse=True)

    payload = {
        "snapshot_time": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "prs": out,
    }
    os.makedirs(os.path.dirname(OUT_FILE), exist_ok=True)
    with open(OUT_FILE, "w") as f:
        json.dump(payload, f, indent=2)

    print(f"Wrote {OUT_FILE} ({len(out)} PRs) at {payload['snapshot_time']}")


if __name__ == "__main__":
    main()
