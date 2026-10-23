import { neon } from "@neondatabase/serverless";
// Read-only backend: every endpoint queries the deployed Postgres database (DATABASE_URL env var on Vercel).
export const sql = neon(process.env.DATABASE_URL);
export function send(res, data, maxAge = 300) {
  res.setHeader("Cache-Control", `s-maxage=${maxAge}, stale-while-revalidate=600`);
  res.status(200).json(data);
}
export function fail(res, e) { res.status(500).json({ error: String(e && e.message || e) }); }
