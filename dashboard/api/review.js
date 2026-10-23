import { sql, send, fail } from "./_db.js";
export default async function handler(req, res) {
  try {
    const id = String(req.query.id || "");
    const [r] = await sql`SELECT * FROM reviews WHERE review_id = ${id}`;
    if (!r) return res.status(404).json({ error: "unknown review_id" });
    send(res, r);
  } catch (e) { fail(res, e); }
}
