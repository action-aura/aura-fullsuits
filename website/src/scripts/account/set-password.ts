import { post, strings, accountUrl, commonError } from './api';
import { $, setBusy, showMessage, clearMessage } from './ui';

// Three ways in:
//   token   : an invitation or reset link, token in the #fragment (#t=...). No session yet.
//   forced  : just signed in with a temporary password (?forced=1). Session cookie, current password re-typed.
//   change  : the "Change password" button on the account page. Same as forced, voluntary.
const t = strings();
const form = $('acct-form') as HTMLFormElement;
const submit = $('acct-submit') as HTMLButtonElement;
const field = (n: string) => (form.elements.namedItem(n) as HTMLInputElement).value;

// Take the token out of the address bar at once, so it is not left in history, screenshots or a shared tab.
const frag = new URLSearchParams(location.hash.replace(/^#/, ''));
const token = frag.get('t') ?? '';
if (location.hash) history.replaceState(null, '', location.pathname + location.search);
const forced = new URLSearchParams(location.search).get('forced') === '1';
const mode: 'token' | 'forced' | 'change' = token ? 'token' : forced ? 'forced' : 'change';

const heading = $('acct-heading');
const lead = $('acct-lead');
if (mode === 'token') {
  heading.textContent = t.setPassword.titleToken;
  lead.textContent = t.setPassword.leadToken;
} else {
  $('row-current').hidden = false;
  heading.textContent = mode === 'forced' ? t.setPassword.titleForced : t.setPassword.titleChange;
  lead.textContent = mode === 'forced' ? t.setPassword.leadForced : t.setPassword.leadChange;
}

form.addEventListener('submit', async (e) => {
  e.preventDefault();
  clearMessage();
  const next = field('next');
  if (!next || !field('again') || (mode !== 'token' && !field('current'))) return showMessage(t.setPassword.required);
  if (next !== field('again')) return showMessage(t.setPassword.mismatch);
  setBusy(submit, true, t.setPassword.saving);
  const res = mode === 'token'
    ? await post('/api/account/set-password', { token, password: next })
    : await post('/api/account/change-password', { currentPassword: field('current'), newPassword: next });

  if (res.status === 200 && res.body.ok) {
    location.assign(mode === 'token' ? accountUrl('sign-in') + '?n=password-saved' : accountUrl());
    return;
  }
  setBusy(submit, false, '');
  if (res.status === 401 && res.body.error === 'session_invalid') return void location.assign(accountUrl('sign-in') + '?n=session');
  if (res.status === 401) return showMessage(t.setPassword.invalidCurrent);
  if (res.status === 422) return showMessage(t.setPassword.reasons[res.body.reason] ?? t.setPassword.reasons.generic);
  if (res.status === 400 && res.body.error === 'token_invalid') {
    $('acct-ask-new').hidden = false;
    return showMessage(t.setPassword.tokenInvalid);
  }
  showMessage(commonError(res, t));
});
