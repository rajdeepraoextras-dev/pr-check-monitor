#!/usr/bin/env python3
"""Fetch gh pr checks for tracked PRs and write docs/data.json (for GitHub Pages)."""
import json, subprocess, datetime, os, sys

ROOT = os.path.dirname(os.path.abspath(__file__))
PRS_FILE = os.path.join(ROOT, "prs.json")
OUT_FILE = os.path.join(ROOT, "docs", "data.json")
PREV_FILE = os.path.join(ROOT, "docs", "data.json")  # read prior submitted flags from same file


def run(cmd):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    return r.stdout


def main():
    prs = json.load(open(PRS_FILE))

    prev_by_key = {}
    if os.path.exists(PREV_FILE):
        try:
            for p in json.load(open(PREV_FILE)).get("prs", []):
                prev_by_key[p["repo"] + p["num"]] = p
        except Exception:
            pass

    out = []
    for entry in prs:
        repo, num = entry["repo"], entry["num"]
        try:
            info = json.loads(run(f'gh pr view {num} -R {repo} --json headRefOid,title,url'))
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
                "overall": overall,
                "passed": passed,
                "total": total,
                "failed": failed,
                "running": running,
                "submitted": submitted,
            })
        except Exception as e:
            out.append({
                "repo": repo.split("/")[-1], "num": num, "title": "(error fetching)",
                "url": f"https://github.com/{repo}/pull/{num}",
                "overall": "error", "passed": 0, "total": 0, "failed": [str(e)], "running": [],
                "submitted": prev_by_key.get(repo.split("/")[-1] + num, {}).get("submitted", False),
            })

    payload = {
        "snapshot_time": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "prs": out,
    }
    os.makedirs(os.path.dirname(OUT_FILE), exist_ok=True)
    with open(OUT_FILE, "w") as f:
        json.dump(payload, f, indent=2)

    print(f"Wrote {OUT_FILE} at {payload['snapshot_time']}")


if __name__ == "__main__":
    main()
