// Shapes Owner's POST /account/summary answer into what the browser may see. Every field is picked by name and
// type-checked, so a new or odd field on the Owner side can never leak through, and a malformed one cannot
// make the page script throw. The contract itself is written down in the slice-S4 progress notes.
import { publicAccount } from './account-gate';

type Obj = Record<string, unknown>;
const isObj = (v: unknown): v is Obj => !!v && typeof v === 'object' && !Array.isArray(v);
const text = (v: unknown, max = 200): string | null => (typeof v === 'string' ? v.slice(0, max) : null);
const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null);
const isoDate = (v: unknown): string | null => (typeof v === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(v) ? v : null);
const list = (v: unknown, max: number): Obj[] => (Array.isArray(v) ? v.filter(isObj).slice(0, max) : []);

export const TRIAL_STATES = ['NOT_STARTED', 'ACTIVE', 'ENDED', 'CONVERTED', 'NONE'] as const;
const PLATFORMS = ['windows', 'android'];

function publicTrial(raw: unknown): Obj {
  const t = isObj(raw) ? raw : {};
  const state = (TRIAL_STATES as readonly string[]).includes(t.state as string) ? t.state : 'NONE';
  return { state, startsOn: isoDate(t.starts_on), endsOn: isoDate(t.ends_on), daysLeft: num(t.days_left) };
}

export function publicSummary(raw: Obj): Obj {
  const seller = isObj(raw.seller) ? raw.seller : null;
  return {
    account: publicAccount(raw.account),
    licences: list(raw.licences, 20).map((l) => ({
      licensePublicId: text(l.license_public_id, 64),
      productCode: text(l.product_code, 32),
      productName: text(l.product_name, 80),
      planCode: text(l.plan_code, 32),
      planName: text(l.plan_name, 80),
      branchLabel: text(l.branch_label, 120),
      deviceAllowance: num(l.device_allowance),
      trial: publicTrial(l.trial),
    })),
    seller: seller ? { name: text(seller.name, 120), phone: text(seller.phone, 32) } : null,
    downloads: list(raw.downloads, 20)
      .filter((d) => typeof d.offer_id === 'string' && PLATFORMS.includes(d.platform as string))
      .map((d) => ({
        offerId: text(d.offer_id, 64),
        platform: d.platform,
        version: text(d.version, 40),
        sizeBytes: num(d.size_bytes),
        filename: text(d.filename, 120),
      })),
  };
}
