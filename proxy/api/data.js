export const config = { runtime: 'edge' };

// Computes the live PR/check snapshot directly from GitHub's API — no
// dependency on the local Mac's launchd jobs at all. Previously this just
// relayed docs/data.json from the repo, which the Mac's fetch.py pushes;
// when the Mac is off/asleep, that file freezes and the dashboard goes
// stale indefinitely. This replicates fetch.py's discover+fetch logic in
// JS, parallelized (fetch() has none of gh CLI's per-call process-spawn
// overhead, so ~100 repos scan in a couple seconds instead of ~2-3min).
//
// Needs GITHUB_TOKEN (Vercel project env var) with **org-wide read
// access** — the previous token (scoped to just this one repo) is NOT
// enough here, since this now lists/reads every repo in ORG. Use a
// classic PAT with the `repo` scope (matches what the Mac's `gh auth
// token` already does) rather than a fine-grained one, since fine-grained
// org access commonly needs org-admin approval.
const ORG = "handshake-project-dynamo";
const AUTHOR = "rajdeepraoextras-dev";
const WINDOW_HOURS = 24;
const TOKEN = process.env.GITHUB_TOKEN;

// Two-tier cache, mirroring fetch.py's fast/full split: discovering which
// PRs exist means scanning every repo (~100 calls) so it's cached longer;
// re-checking known PRs' check-runs is cheap (~2 calls/PR) so it refreshes
// often. Keeps total GitHub API usage well under the 5000/hr authenticated
// budget even under steady traffic.
const DISCOVER_TTL_MS = 4 * 60 * 1000;
const REFRESH_TTL_MS = 25 * 1000;

function ghHeaders() {
  const h = { Accept: "application/vnd.github+json", "User-Agent": "prmon-live" };
  if (TOKEN) h.Authorization = `Bearer ${TOKEN}`;
  return h;
}
async function ghJSON(path) {
  const r = await fetch(`https://api.github.com${path}`, { headers: ghHeaders() });
  if (!r.ok) {
    const body = await r.text().catch(() => "");
    throw new Error(`${path} -> ${r.status} ${body.slice(0, 200)}`);
  }
  return r.json();
}
function cleanName(n) { return (n || "").replace("review / ", ""); }

// Runs `fn` over `items` with at most `size` in flight at once. A single
// Promise.all over ~100 repos was tripping GitHub's secondary/abuse rate
// limit (a burst-concurrency guard, separate from the 5000/hr quota) —
// especially since each Vercel edge region keeps its own cache and can
// independently kick off its own ~100-way burst. Chunking is gentler and
// makes results reproducible across regions.
async function pMapChunked(items, size, fn) {
  const out = [];
  for (let i = 0; i < items.length; i += size) {
    const chunk = items.slice(i, i + size);
    out.push(...await Promise.all(chunk.map(fn)));
  }
  return out;
}

async function listOrgRepos() {
  const repos = [];
  for (let page = 1; page <= 5; page++) {
    const batch = await ghJSON(`/orgs/${ORG}/repos?per_page=100&page=${page}&type=all`);
    repos.push(...batch.map(r => r.name));
    if (batch.length < 100) break;
  }
  return repos;
}

async function reviewDecisionFor(repoFull, num) {
  try {
    const reviews = await ghJSON(`/repos/${repoFull}/pulls/${num}/reviews?per_page=100`);
    const latest = {};
    for (const r of reviews) if (r.state !== "COMMENTED") latest[r.user.login] = r.state;
    const states = Object.values(latest);
    if (states.includes("CHANGES_REQUESTED")) return "CHANGES_REQUESTED";
    if (states.includes("APPROVED")) return "APPROVED";
    return "";
  } catch (e) { return ""; }
}

let discoverCache = { list: null, at: 0 };
let lastDebug = {};
async function getKnownPRs() {
  const now = Date.now();
  if (discoverCache.list && now - discoverCache.at <= DISCOVER_TTL_MS) return discoverCache.list;
  let repos;
  try {
    repos = await listOrgRepos();
  } catch (e) {
    lastDebug = { repoListError: String(e.message || e) };
    if (discoverCache.list) return discoverCache.list;
    throw e;
  }
  const cutoff = now - WINDOW_HOURS * 3600 * 1000;
  let errorCount = 0;
  const sampleErr = [];
  const rawCounts = [];
  const perRepo = await pMapChunked(repos, 20, async name => {
    try {
      const prs = await ghJSON(`/repos/${ORG}/${name}/pulls?state=open&per_page=50`);
      rawCounts.push(prs.length);
      return prs
        .filter(p => p.user && p.user.login === AUTHOR && new Date(p.created_at).getTime() >= cutoff)
        .map(p => ({ repo: `${ORG}/${name}`, num: String(p.number) }));
    } catch (e) { errorCount++; if (sampleErr.length < 3) sampleErr.push(String(e.message || e)); return []; }
  });
  lastDebug = {
    repoCount: repos.length, errorCount, sampleErr,
    totalOpenPRsSeen: rawCounts.reduce((a, b) => a + b, 0),
    reposWithPRs: rawCounts.filter(n => n > 0).length,
  };
  // If too many per-repo calls failed (rate limiting, a transient GitHub
  // blip), the found list is unreliable — don't let an incomplete scan
  // overwrite a good cache with a falsely-empty/short result. Serve the
  // last good list a little longer instead; only accept the new one once
  // enough of the org actually responded.
  if (repos.length && errorCount / repos.length > 0.15) {
    if (discoverCache.list) return discoverCache.list;
    throw new Error(`discovery failed on ${errorCount}/${repos.length} repos`);
  }
  const found = perRepo.flat();
  // reviewDecision only needs refreshing on the slow discovery cadence
  const withReview = await pMapChunked(found, 20, async p => ({
    ...p, reviewDecision: await reviewDecisionFor(p.repo, p.num),
  }));
  discoverCache = { list: withReview, at: now };
  return withReview;
}

async function fetchPR(entry) {
  const { repo: repoFull, num, reviewDecision } = entry;
  const info = await ghJSON(`/repos/${repoFull}/pulls/${num}`);
  const sha = info.head.sha;
  const runsResp = await ghJSON(`/repos/${repoFull}/commits/${sha}/check-runs?per_page=100`);
  const checkruns = runsResp.check_runs || [];
  const checks = checkruns.map(c => {
    const started = c.started_at ? new Date(c.started_at).getTime() : null;
    const completed = c.completed_at ? new Date(c.completed_at).getTime() : null;
    const dur = (started != null && completed != null) ? Math.round((completed - started) / 1000) : null;
    let state;
    if (c.status === "completed") state = c.conclusion === "success" ? "passed" : (["skipped", "neutral"].includes(c.conclusion) ? "skipped" : "failed");
    else if (c.status === "in_progress") state = "running";
    else state = "queued";
    return {
      name: cleanName(c.name), state, conclusion: c.conclusion,
      started_at: c.started_at, completed_at: c.completed_at, duration_sec: dur,
      url: c.html_url, app: c.app && c.app.slug,
    };
  });
  const order = { failed: 0, running: 1, queued: 2, passed: 3, skipped: 4 };
  checks.sort((a, b) => (order[a.state] - order[b.state]) || a.name.localeCompare(b.name));
  const passed = checks.filter(c => c.state === "passed").length;
  const failed = checks.filter(c => c.state === "failed").map(c => c.name);
  const running = checks.filter(c => c.state === "running").map(c => ({ name: c.name, started_at: c.started_at }));
  const queued = checks.filter(c => c.state === "queued").map(c => c.name);
  const skipped = checks.filter(c => c.state === "skipped").length;
  const total = checks.length;
  const overall = failed.length ? "failing" : ((running.length || queued.length) ? "running" : "passed");
  const name = repoFull.split("/").pop();
  return {
    repo: name, full_repo: repoFull, num,
    title: info.title, url: info.html_url,
    createdAt: info.created_at, updatedAt: info.updated_at, sha,
    draft: !!info.draft, mergeable: info.mergeable,
    reviewDecision: reviewDecision || "",
    additions: info.additions || 0, deletions: info.deletions || 0, changedFiles: info.changed_files || 0,
    head: info.head.ref, base: info.base.ref,
    labels: (info.labels || []).map(l => l.name),
    overall, passed, total, skipped, failed, running, queued, checks,
    submitted: false,
  };
}

let snapCache = { body: null, at: 0 };
async function computeSnapshot() {
  const known = await getKnownPRs();
  const out = await pMapChunked(known, 20, p => fetchPR(p).catch(e => ({
    repo: p.repo.split("/").pop(), full_repo: p.repo, num: p.num, title: "(error fetching)",
    url: `https://github.com/${p.repo}/pull/${p.num}`, overall: "error",
    passed: 0, total: 0, skipped: 0, failed: [String(e.message || e)], running: [], queued: [], checks: [], labels: [],
    submitted: false,
  })));
  out.sort((a, b) => (b.createdAt || "").localeCompare(a.createdAt || ""));
  return { snapshot_time: new Date().toISOString(), config: { org: ORG, author: AUTHOR, window_hours: WINDOW_HOURS }, prs: out, _debug: lastDebug };
}

export default async function handler() {
  const now = Date.now();
  if (!snapCache.body || now - snapCache.at > REFRESH_TTL_MS) {
    try {
      const snap = await computeSnapshot();
      snapCache = { body: JSON.stringify(snap), at: now };
    } catch (e) {
      if (!snapCache.body) {
        snapCache = {
          body: JSON.stringify({
            snapshot_time: new Date().toISOString(),
            config: { org: ORG, author: AUTHOR, window_hours: WINDOW_HOURS },
            prs: [], error: String(e.message || e),
          }),
          at: now,
        };
      }
    }
  }
  return new Response(snapCache.body, {
    headers: {
      "Content-Type": "application/json",
      "Cache-Control": "no-store, must-revalidate",
      "Access-Control-Allow-Origin": "*",
    },
  });
}
