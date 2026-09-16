#!/usr/bin/env python3
"""
Discover open PRs authored by config.author in config.org opened within
config.window_hours, fetch their check status, and write docs/data.json
(for GitHub Pages). Also appends state-change events to docs/events.json.

Any PRs listed in prs.json are always included too (manual pins), regardless
of age.
"""
import json, subprocess, datetime, os

ROOT = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(ROOT, "config.json")
PRS_FILE = os.path.join(ROOT, "prs.json")
OUT_FILE = os.path.join(ROOT, "docs", "data.json")
EVENTS_FILE = os.path.join(ROOT, "docs", "events.json")
MAX_EVENTS = 400


def run(cmd):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    return r.stdout


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


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


def parse_ts(s):
    if not s:
        return None
    try:
        return datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def clean_name(n):
    return (n or "").replace("review / ", "")


def fetch_pr(repo, num, prev):
    info = json.loads(run(
        f'gh pr view {num} -R {repo} --json '
        f'headRefOid,title,url,createdAt,updatedAt,isDraft,mergeable,reviewDecision,'
        f'additions,deletions,changedFiles,headRefName,baseRefName,labels'
    ))
    sha = info["headRefOid"]
    raw = run(f'gh api "repos/{repo}/commits/{sha}/check-runs" --paginate')
    checkruns = []
    for line in raw.strip().splitlines():
        if not line.strip():
            continue
        try:
            checkruns.extend(json.loads(line).get("check_runs", []))
        except Exception:
            pass

    checks = []
    for c in checkruns:
        st = c.get("status")            # queued | in_progress | completed
        con = c.get("conclusion")       # success | failure | cancelled | skipped | neutral | timed_out | action_required | None
        started = parse_ts(c.get("started_at"))
        completed = parse_ts(c.get("completed_at"))
        dur = None
        if started and completed:
            dur = int((completed - started).total_seconds())
        if st == "completed":
            state = "passed" if con == "success" else ("skipped" if con in ("skipped", "neutral") else "failed")
        elif st == "in_progress":
            state = "running"
        else:
            state = "queued"
        checks.append({
            "name": clean_name(c.get("name")),
            "state": state,
            "conclusion": con,
            "started_at": c.get("started_at"),
            "completed_at": c.get("completed_at"),
            "duration_sec": dur,
            "url": c.get("html_url"),
            "app": ((c.get("app") or {}).get("slug")),
        })
    checks.sort(key=lambda x: ({"failed": 0, "running": 1, "queued": 2, "passed": 3, "skipped": 4}[x["state"]], x["name"]))

    passed = sum(1 for c in checks if c["state"] == "passed")
    failed = [c["name"] for c in checks if c["state"] == "failed"]
    running = [{"name": c["name"], "started_at": c["started_at"]} for c in checks if c["state"] == "running"]
    queued = [c["name"] for c in checks if c["state"] == "queued"]
    skipped = sum(1 for c in checks if c["state"] == "skipped")
    total = len(checks)
    overall = "failing" if failed else ("running" if (running or queued) else "passed")

    return {
        "repo": repo.split("/")[-1],
        "full_repo": repo,
        "num": num,
        "title": info["title"],
        "url": info["url"],
        "createdAt": info.get("createdAt"),
        "updatedAt": info.get("updatedAt"),
        "sha": sha,
        "draft": bool(info.get("isDraft")),
        "mergeable": info.get("mergeable"),
        "reviewDecision": info.get("reviewDecision") or "",
        "additions": info.get("additions", 0),
        "deletions": info.get("deletions", 0),
        "changedFiles": info.get("changedFiles", 0),
        "head": info.get("headRefName"),
        "base": info.get("baseRefName"),
        "labels": [l.get("name") for l in (info.get("labels") or [])],
        "overall": overall,
        "passed": passed,
        "total": total,
        "skipped": skipped,
        "failed": failed,
        "running": running,
        "queued": queued,
        "checks": checks,
        "submitted": prev.get("submitted", False),
        "first_seen": prev.get("first_seen") or now_iso(),
        "last_change": prev.get("last_change") or now_iso(),
    }


def diff_events(prev, cur, ts):
    """Return list of events describing what changed between prev and cur snapshot of one PR."""
    ev = []
    key = f'{cur["repo"]}#{cur["num"]}'
    if not prev:
        ev.append({"ts": ts, "pr": key, "type": "discovered", "msg": f'PR discovered: {cur["title"]}'})
        return ev
    if prev.get("sha") and prev.get("sha") != cur.get("sha"):
        ev.append({"ts": ts, "pr": key, "type": "push", "msg": f'New commit pushed ({cur["sha"][:7]})'})
    if prev.get("overall") != cur.get("overall"):
        ev.append({"ts": ts, "pr": key, "type": cur["overall"], "msg": f'Status {prev.get("overall")} → {cur["overall"]}'})
    pf = set(prev.get("failed") or []); cf = set(cur.get("failed") or [])
    for n in sorted(cf - pf):
        ev.append({"ts": ts, "pr": key, "type": "failing", "msg": f'Check failed: {n}'})
    for n in sorted(pf - cf):
        ev.append({"ts": ts, "pr": key, "type": "passed", "msg": f'Check recovered: {n}'})
    pp = {c["name"] for c in (prev.get("checks") or []) if c["state"] == "passed"}
    cp = {c["name"] for c in (cur.get("checks") or []) if c["state"] == "passed"}
    newly_passed = sorted(cp - pp)
    if newly_passed and len(newly_passed) <= 3:
        for n in newly_passed:
            ev.append({"ts": ts, "pr": key, "type": "passed", "msg": f'Check passed: {n}'})
    elif newly_passed:
        ev.append({"ts": ts, "pr": key, "type": "passed", "msg": f'{len(newly_passed)} checks passed'})
    if prev.get("reviewDecision") != cur.get("reviewDecision") and cur.get("reviewDecision"):
        ev.append({"ts": ts, "pr": key, "type": "review", "msg": f'Review: {cur["reviewDecision"].replace("_"," ").lower()}'})
    return ev


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
                prev_by_key[p["repo"] + "#" + p["num"]] = p
        except Exception:
            pass

    events = []
    if os.path.exists(EVENTS_FILE):
        try:
            events = json.load(open(EVENTS_FILE))
        except Exception:
            events = []

    ts = now_iso()
    out = []
    for entry in prs:
        repo, num = entry["repo"], entry["num"]
        key = repo.split("/")[-1] + "#" + num
        prev = prev_by_key.get(key, {})
        try:
            cur = fetch_pr(repo, num, prev)
            new_ev = diff_events(prev, cur, ts)
            if new_ev:
                cur["last_change"] = ts
            events.extend(new_ev)
            out.append(cur)
        except Exception as e:
            out.append({
                "repo": repo.split("/")[-1], "full_repo": repo, "num": num, "title": "(error fetching)",
                "url": f"https://github.com/{repo}/pull/{num}",
                "overall": "error", "passed": 0, "total": 0, "skipped": 0, "failed": [str(e)],
                "running": [], "queued": [], "checks": [], "labels": [],
                "submitted": prev.get("submitted", False),
                "first_seen": prev.get("first_seen") or ts,
                "last_change": prev.get("last_change") or ts,
            })

    # newest first
    out.sort(key=lambda p: p.get("createdAt") or "", reverse=True)

    events = events[-MAX_EVENTS:]

    payload = {
        "snapshot_time": ts,
        "config": {"org": org, "author": author, "window_hours": window_hours},
        "prs": out,
    }
    os.makedirs(os.path.dirname(OUT_FILE), exist_ok=True)
    with open(OUT_FILE, "w") as f:
        json.dump(payload, f, indent=2)
    with open(EVENTS_FILE, "w") as f:
        json.dump(events, f, indent=1)

    print(f"Wrote {OUT_FILE} ({len(out)} PRs, {len(events)} events) at {ts}")


if __name__ == "__main__":
    main()
