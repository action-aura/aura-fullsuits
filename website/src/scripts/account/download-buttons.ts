// Download buttons shared by the account page and the downloads page. Pressing one asks for a fresh link
// (10 minutes, 3 uses) and then navigates to it; the browser streams the file from our own address.
import { post, accountUrl, commonError, fill, lang, type Strings } from './api';
import { el, setBusy, showMessage, clearMessage } from './ui';
import type { Download } from './summary';

export function renderDownloads(list: HTMLElement, downloads: Download[], t: Strings, withDetails: boolean): void {
  list.replaceChildren();
  for (const d of downloads) {
    const li = el('li', 'acct-download');
    const text = el('div', 'acct-download-text');
    text.append(el('strong', undefined, d.platform === 'windows' ? t.downloads.windows : t.downloads.android));
    const meta: string[] = [];
    if (d.version) meta.push(fill(t.downloads.version, { v: d.version }));
    if (d.sizeBytes) meta.push(fill(t.downloads.size, { size: Math.max(1, Math.round(d.sizeBytes / 1_000_000)) }));
    if (withDetails && meta.length) text.append(el('span', 'acct-meta', meta.join(' / ')));
    const button = el('button', 'btn-send-wa', t.downloads.button);
    button.type = 'button';
    button.dataset.label = t.downloads.button;
    button.dataset.offer = d.offerId;
    button.addEventListener('click', () => void startDownload(button, d.offerId, t));
    li.append(text, button);
    list.append(li);
  }
}

async function startDownload(button: HTMLButtonElement, offerId: string, t: Strings): Promise<void> {
  clearMessage();
  setBusy(button, true, t.downloads.preparing);
  const res = await post('/api/account/downloads/authorize', { offerId, lang: lang() });
  setBusy(button, false, '');
  if (res.status === 200 && res.body.ok && typeof res.body.url === 'string' && res.body.url.startsWith('/api/account/downloads/fetch?')) {
    location.assign(res.body.url);
    return;
  }
  if (res.status === 401) return void location.assign(accountUrl('sign-in') + '?n=session');
  if (res.status === 403) return void location.assign(accountUrl('set-password') + '?forced=1');
  showMessage(res.status === 404 ? t.downloads.unavailable : commonError(res, t));
}
