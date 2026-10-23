import { sql, send, fail } from "./_db.js";
export default async function handler(req, res) {
  try {
    const [rec] = await sql`SELECT * FROM recommendations ORDER BY id DESC LIMIT 1`;
    const claims = await sql`SELECT c.*, i.name FROM claims c LEFT JOIN issues i USING(issue_id) ORDER BY claim_id`;
    send(res, { ...rec, claims });
  } catch (e) { fail(res, e); }
}
