import { describe, it, expect } from 'vitest';
import worker, { RETENTION_SQL } from '../../retention/index';

describe('retention worker', () => {
  it('deletes leads older than 24 months, nothing else', () => {
    expect(RETENTION_SQL).toBe("DELETE FROM leads WHERE created_at < datetime('now','-24 months')");
  });
  it('runs the statement once per scheduled tick and reports the count', async () => {
    const calls: string[] = [];
    const env = { DB: { prepare: (sql: string) => ({ run: async () => { calls.push(sql); return { meta: { changes: 3 } }; } }) } };
    const out = await worker.scheduled({}, env as any, {});
    expect(calls).toEqual([RETENTION_SQL]);
    expect(out).toEqual({ deleted: 3 });
  });
});
