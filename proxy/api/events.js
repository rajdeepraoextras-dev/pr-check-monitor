export const config = { runtime: 'edge' };

const REPO = "rajdeepraoextras-dev/pr-check-monitor";
const TOKEN = process.env.GITHUB_TOKEN;
const TTL_MS = TOKEN ? 3000 : 75000;
let cache = { body: null, at: 0 };

export default async function handler() {
  const now = Date.now();
  if (!cache.body || now - cache.at > TTL_MS) {
    try {
      const headers = { Accept: "application/vnd.github.raw+json", "User-Agent": "prmon-proxy" };
      if (TOKEN) headers.Authorization = `Bearer ${TOKEN}`;
      const r = await fetch(
        `https://api.github.com/repos/${REPO}/contents/docs/events.json?ref=main`,
        { headers }
      );
      if (r.ok) cache = { body: await r.text(), at: now };
    } catch (e) {}
  }
  return new Response(cache.body || "[]", {
    headers: {
      "Content-Type": "application/json",
      "Cache-Control": "no-store, must-revalidate",
      "Access-Control-Allow-Origin": "*",
      "X-Prmon-Auth": TOKEN ? "token" : "anon",
    },
  });
}
