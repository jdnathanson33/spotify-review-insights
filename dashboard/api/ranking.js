import { sql, send, fail } from "./_db.js";
export default async function handler(req, res) {
  try { send(res, await sql`SELECT * FROM issues ORDER BY rank`); } catch (e) { fail(res, e); }
}
