import { sql, send, fail } from "./_db.js";
export default async function handler(req, res) {
  try {
    const id = String(req.query.id || "");
    const limit = Math.min(parseInt(req.query.limit || "25", 10) || 25, 100);
    const [issue] = await sql`SELECT * FROM issues WHERE issue_id = ${id}`;
    if (!issue) return res.status(404).json({ error: "unknown issue" });
    const monthly = await sql`SELECT month, issue_complaints, all_complaints FROM monthly WHERE issue_id = ${id} ORDER BY month`;
    const evidence = await sql`SELECT review_id, review_ts, likes, intent, severity, sentiment, evidence_quote, cache_source_id
                               FROM reviews WHERE issue_id = ${id} ORDER BY likes DESC NULLS LAST, severity DESC, review_id LIMIT ${limit}`;
    send(res, { issue, monthly, evidence });
  } catch (e) { fail(res, e); }
}
