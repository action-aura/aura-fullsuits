import { post, strings, accountUrl, commonError } from './api';
import { $, setBusy, showMessage, clearMessage } from './ui';

const t = strings();
const form = $('acct-form') as HTMLFormElement;
const submit = $('acct-submit') as HTMLButtonElement;

// A notice carried over from another page ("your password is saved", "you are signed out").
const notice = new URLSearchParams(location.search).get('n');
if (notice === 'password-saved') showMessage(t.signIn.passwordSaved, 'ok');
else if (notice === 'signed-out') showMessage(t.signIn.signedOut, 'ok');
else if (notice === 'session') showMessage(t.common.sessionEnded, 'error');

form.addEventListener('submit', async (e) => {
  e.preventDefault();
  clearMessage();
  const email = (form.elements.namedItem('email') as HTMLInputElement).value.trim();
  const password = (form.elements.namedItem('password') as HTMLInputElement).value;
  if (!email || !password) return showMessage(t.signIn.required);
  setBusy(submit, true, t.signIn.submitting);
  const res = await post('/api/account/sign-in', { email, password });
  if (res.status === 200 && res.body.ok) {
    // A temporary password must be replaced before anything else; the account page and the downloads
    // page check the same flag, so skipping this page changes nothing.
    location.assign(res.body.account?.passwordMustChange ? accountUrl('set-password') + '?forced=1' : accountUrl());
    return;
  }
  setBusy(submit, false, '');
  (form.elements.namedItem('password') as HTMLInputElement).value = '';
  showMessage(res.status === 401 ? t.signIn.invalid : commonError(res, t));
});
