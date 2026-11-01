import { post, strings, accountUrl, lang, fill, type Strings } from './api';
import { $, el, reveal, phoneDigits } from './ui';
import { loadSummary, type Licence, type Summary } from './summary';
import { renderDownloads } from './download-buttons';

const t = strings();

function formatDate(iso: string): string {
  const [y, m, d] = iso.split('-').map(Number);
  // Western digits in Arabic too (the site never uses Arabic-Indic digits). UTC so the day cannot shift.
  const locale = lang() === 'ar' ? 'ar-u-nu-latn' : 'en-GB';
  return new Intl.DateTimeFormat(locale, { dateStyle: 'long', timeZone: 'UTC' }).format(new Date(Date.UTC(y, m - 1, d)));
}

function trialLine(l: Licence, s: Strings): string | null {
  const tr = l.trial;
  switch (tr.state) {
    case 'NOT_STARTED': return s.home.trial.notStarted;
    case 'ENDED': return s.home.trial.ended;
    case 'CONVERTED': return s.home.trial.converted;
    case 'ACTIVE': {
      if (tr.daysLeft === 0) return s.home.trial.lastDay;
      const date = tr.endsOn ? formatDate(tr.endsOn) : '';
      return fill(tr.daysLeft === 1 ? s.home.trial.activeOne : s.home.trial.active, { n: tr.daysLeft ?? '', date });
    }
    default: return null;
  }
}

function card(titleText: string): { box: HTMLElement; body: HTMLElement } {
  const box = el('section', 'acct-card');
  box.append(el('h2', undefined, titleText));
  const body = el('div', 'acct-card-body');
  box.append(body);
  return { box, body };
}

function row(label: string, value: string): HTMLElement {
  const r = el('div', 'acct-row');
  r.append(el('span', 'acct-label', label), el('span', 'acct-value', value));
  return r;
}

function renderPackage(root: HTMLElement, s: Summary): void {
  const { box, body } = card(t.home.package.title);
  if (s.licences.length === 0) body.append(el('p', undefined, t.home.package.none));
  for (const l of s.licences) {
    const group = el('div', 'acct-licence');
    // Owner's product_name is English; the page shows the product in the visitor's language when it knows the code.
    const product = (l.productCode && t.home.package.productNames[l.productCode]) || l.productName;
    if (product) group.append(row(t.home.package.product, product));
    if (l.planName) group.append(row(t.home.package.plan, l.planName));
    if (l.deviceAllowance !== null) group.append(row(t.home.package.devices, String(l.deviceAllowance)));
    if (l.branchLabel) group.append(row(t.home.package.branch, l.branchLabel));
    const line = trialLine(l, t);
    if (line) {
      const trial = el('p', 'acct-trial', line);
      trial.dataset.state = l.trial.state;
      group.append(trial);
    }
    body.append(group);
  }
  root.append(box);
}

function renderPay(root: HTMLElement, s: Summary): void {
  const { box, body } = card(t.home.pay.title);
  body.append(el('p', undefined, t.home.pay.body));
  const seller = s.seller;
  if (seller && (seller.name || seller.phone)) {
    if (seller.name) body.append(row(t.home.pay.seller, seller.name));
    if (seller.phone) {
      const digits = phoneDigits(seller.phone);
      const links = el('p', 'acct-contact');
      const call = el('a', 'acct-link', t.home.pay.call + ' ');
      call.href = `tel:${digits}`;
      const num = el('bdi', undefined, seller.phone);
      num.dir = 'ltr';
      call.append(num);
      const wa = el('a', 'acct-link', t.home.pay.whatsapp);
      wa.href = `https://wa.me/${digits.replace('+', '')}`;
      wa.rel = 'noopener';
      links.append(call, wa);
      body.append(links);
    }
  } else {
    body.append(el('p', 'acct-note', t.home.pay.noSeller));
  }
  root.append(box);
}

function renderDownloadCard(root: HTMLElement, s: Summary): void {
  const { box, body } = card(t.home.downloads.title);
  body.append(el('p', undefined, t.home.downloads.lead));
  const list = el('ul', 'acct-downloads');
  body.append(list);
  renderDownloads(list, s.downloads, t, false);
  const more = el('a', 'acct-link', t.home.downloads.more);
  more.href = accountUrl('downloads');
  body.append(more);
  root.append(box);
}

function renderFooter(root: HTMLElement, s: Summary): void {
  const bar = el('div', 'acct-bar');
  const who = el('p', 'acct-who', `${t.home.signedInAs} `);
  const mail = el('bdi', undefined, s.account.email);
  mail.dir = 'ltr';
  who.append(mail);
  const change = el('a', 'acct-link', t.common.changePassword);
  change.href = accountUrl('set-password');
  const out = el('button', 'btn-send', t.common.signOut);
  out.type = 'button';
  out.addEventListener('click', async () => {
    out.disabled = true;
    await post('/api/account/sign-out');
    location.assign(accountUrl('sign-in') + '?n=signed-out');
  });
  bar.append(who, change, out);
  root.append(bar);
}

void loadSummary(t).then((s) => {
  if (!s) return;
  const root = $('acct-root');
  renderPackage(root, s);
  renderPay(root, s);
  renderDownloadCard(root, s);
  renderFooter(root, s);
  reveal();
});
