import { sql, send, fail } from "./_db.js";
export default async function handler(req, res) {
  try {
    const rows = await sql`SELECT key, value FROM metrics`;
    const m = Object.fromEntries(rows.map(r => [r.key, r.value]));
    const live = await sql`SELECT status, count(*)::int AS n FROM reviews GROUP BY status`;
    m.live_status_counts = Object.fromEntries(live.map(r => [r.status, r.n]));
    send(res, m);
  } catch (e) { fail(res, e); }
}
