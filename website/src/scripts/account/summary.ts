// Loads /api/account/summary and applies the two rules every signed-in page shares:
//   - no session            -> sign-in page
//   - temporary password    -> forced password change (nothing else is usable until then)
import { post, accountUrl, commonError, lang, type Strings } from './api';
import { showMessage } from './ui';

export interface Download { offerId: string; platform: 'windows' | 'android'; version: string | null; sizeBytes: number | null; filename: string | null }
export interface Trial { state: string; startsOn: string | null; endsOn: string | null; daysLeft: number | null }
export interface Licence {
  licensePublicId: string | null; productCode: string | null; productName: string | null; planName: string | null; planCode: string | null;
  branchLabel: string | null; deviceAllowance: number | null; trial: Trial;
}
export interface Summary {
  account: { email: string; customerName: string | null; passwordMustChange: boolean };
  licences: Licence[];
  seller: { name: string | null; phone: string | null } | null;
  downloads: Download[];
}

/** Resolves with the summary, or never resolves (the page is being redirected, or an error is shown). */
export async function loadSummary(t: Strings): Promise<Summary | null> {
  const res = await post('/api/account/summary', { lang: lang() });
  if (res.status === 401) {
    location.assign(accountUrl('sign-in') + '?n=session');
    return null;
  }
  if (res.status !== 200 || !res.body.ok) {
    document.getElementById('acct-loading')?.setAttribute('hidden', '');
    showMessage(commonError(res, t));
    return null;
  }
  const s = res.body as unknown as Summary;
  if (s.account.passwordMustChange) {
    location.assign(accountUrl('set-password') + '?forced=1');
    return null;
  }
  return s;
}
