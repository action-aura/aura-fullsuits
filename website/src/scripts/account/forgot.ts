import { post, strings, lang, commonError } from './api';
import { $, setBusy, showMessage, clearMessage } from './ui';

const t = strings();
const form = $('acct-form') as HTMLFormElement;
const submit = $('acct-submit') as HTMLButtonElement;

form.addEventListener('submit', async (e) => {
  e.preventDefault();
  clearMessage();
  const email = (form.elements.namedItem('email') as HTMLInputElement).value.trim();
  if (!email) return showMessage(t.forgot.required);
  const turnstile = (form.querySelector('[name="cf-turnstile-response"]') as HTMLInputElement | null)?.value ?? '';
  setBusy(submit, true, t.forgot.sending);
  const res = await post('/api/account/forgot', { email, lang: lang(), turnstile });
  if (res.status === 202) {
    form.hidden = true;
    showMessage(t.forgot.done, 'ok');
    return;
  }
  setBusy(submit, false, '');
  // A Turnstile token works once; any failed try needs a fresh one.
  (window as any).turnstile?.reset?.();
  showMessage(res.status === 403 ? t.forgot.challenge : commonError(res, t));
});
