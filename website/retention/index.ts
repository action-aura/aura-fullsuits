// Scheduled deletion of demo leads older than 24 months. The Privacy page promises this
// retention; Pages Functions cannot run on a schedule, so this tiny Worker shares the D1
// binding and runs daily. Logs the count so a silent no-op is distinguishable from "ran".
import type { D1Database } from '../functions/env';

export const RETENTION_SQL = "DELETE FROM leads WHERE created_at < datetime('now','-24 months')";

type Env = { DB: D1Database };

export default {
  async scheduled(_controller: unknown, env: Env, _ctx: unknown): Promise<{ deleted: number }> {
    const r = await env.DB.prepare(RETENTION_SQL).run();
    const deleted = Number(r.meta?.changes ?? 0);
    console.log(`retention: deleted ${deleted} lead(s) older than 24 months`);
    return { deleted };
  },
};
