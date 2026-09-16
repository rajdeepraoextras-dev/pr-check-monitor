#!/usr/bin/env python3
"""
Two modes, run by two separate launchd jobs:

  python3 fetch.py            (slow/full — ~every 2 min)
    Scans every repo in config.org for open PRs by config.author opened
    within config.window_hours (the expensive part: ~1 GraphQL call per
    repo), refreshes full metadata + reviewDecision, and rebuilds the
    tracked-PR list. Also merges in any manual pins from prs.json.

  python3 fetch.py --fast     (fast/refresh — ~every 20-25s)
    Skips discovery entirely and just re-fetches check-run status for the
    PRs already in the last docs/data.json snapshot, using REST calls only
    (kept out of the GraphQL budget the slow scan uses) so it can run much
    more often without tripping GitHub's rate limits. reviewDecision is
    carried forward unchanged between slow runs.

Both write docs/data.json + docs/events.json (state-change log, capped at
MAX_EVENTS) for GitHub Pages.
"""
import json, subprocess, datetime, os, sys

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


def fetch_pr(repo, num, prev, refresh_review=True):
    # Pure REST (GET /repos/{repo}/pulls/{num}) — separate rate-limit budget
    # from the GraphQL calls discovery uses, so the fast loop never competes
    # with the slow scan for the same 5000/hr quota.
    info = json.loads(run(
        f'gh api repos/{repo}/pulls/{num} --jq '
        '\'{title, url: .html_url, createdAt: .created_at, updatedAt: .updated_at, '
        'headRefOid: .head.sha, isDraft: .draft, mergeable, '
        'additions, deletions, changedFiles: .changed_files, '
        'headRefName: .head.ref, baseRefName: .base.ref, labels: [.labels[].name]}\''
    ))
    review_decision = prev.get("reviewDecision", "")
    if refresh_review:
        try:
            review_decision = run(f'gh pr view {num} -R {repo} --json reviewDecision --jq .reviewDecision').strip()
        except Exception:
            pass
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
        "reviewDecision": review_decision or "",
        "additions": info.get("additions", 0),
        "deletions": info.get("deletions", 0),
        "changedFiles": info.get("changedFiles", 0),
        "head": info.get("headRefName"),
        "base": info.get("baseRefName"),
        "labels": info.get("labels") or [],
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
    pr_running = {c["name"] for c in (prev.get("checks") or []) if c["state"] == "running"}
    cr_running = {c["name"] for c in (cur.get("checks") or []) if c["state"] == "running"}
    for n in sorted(cr_running - pr_running):
        ev.append({"ts": ts, "pr": key, "type": "started", "msg": f'Check started: {n}'})
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
    fast = "--fast" in sys.argv

    config = json.load(open(CONFIG_FILE)) if os.path.exists(CONFIG_FILE) else {}
    org = config.get("org")
    author = config.get("author")
    window_hours = config.get("window_hours", 24)

    prev_snapshot = {}
    if os.path.exists(OUT_FILE):
        try:
            prev_snapshot = json.load(open(OUT_FILE))
        except Exception:
            prev_snapshot = {}
    prev_by_key = {p["repo"] + "#" + p["num"]: p for p in prev_snapshot.get("prs", [])}

    if fast:
        # No discovery scan — just re-check the PRs already being tracked.
        prs = [
            {"repo": p.get("full_repo") or f'{org}/{p["repo"]}', "num": p["num"]}
            for p in prev_snapshot.get("prs", [])
        ]
    else:
        prs = discover_prs(org, author, window_hours) if org and author else []
        pinned = json.load(open(PRS_FILE)) if os.path.exists(PRS_FILE) else []
        seen = {(p["repo"], p["num"]) for p in prs}
        for p in pinned:
            if (p["repo"], p["num"]) not in seen:
                prs.append(p)
                seen.add((p["repo"], p["num"]))

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
            cur = fetch_pr(repo, num, prev, refresh_review=not fast)
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
    # Atomic write (temp file + rename): the fast and full jobs both write
    # this file directly and can genuinely overlap in time, since only the
    # short git-commit step is lock-serialized between them (see run.sh) —
    # this guarantees whichever one finishes second fully replaces the file
    # in one step rather than the two writes ever interleaving.
    os.makedirs(os.path.dirname(OUT_FILE), exist_ok=True)
    for path, data, kw in ((OUT_FILE, payload, {"indent": 2}), (EVENTS_FILE, events, {"indent": 1})):
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, **kw)
        os.replace(tmp, path)

    print(f"[{'fast' if fast else 'full'}] Wrote {OUT_FILE} ({len(out)} PRs, {len(events)} events) at {ts}")


if __name__ == "__main__":
    main()
