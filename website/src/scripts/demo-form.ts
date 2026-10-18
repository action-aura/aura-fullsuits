import { validateLead, type LeadError } from '../lib/lead';
import { waLink } from '../lib/lang';

const FIELDS = ['name', 'phone', 'businessType', 'businessName', 'branches', 'message'] as const;
type Field = (typeof FIELDS)[number];

const kebab = (s: string) => s.replace(/[A-Z]/g, (c) => '-' + c.toLowerCase());

function init(form: HTMLFormElement): void {
  const msg = (key: string): string => form.getAttribute('data-err-' + kebab(key)) ?? '';
  const sendBtn = form.querySelector<HTMLButtonElement>('#demo-send')!;
  const waBtn = form.querySelector<HTMLButtonElement>('#demo-send-wa')!;
  const errBox = document.getElementById('demo-error') as HTMLElement;
  const errText = document.getElementById('demo-error-text') as HTMLElement;
  const success = document.getElementById('demo-success') as HTMLElement;
  const lang = form.getAttribute('data-lang') === 'ar' ? 'ar' : 'en';

  const control = (f: Field) => form.elements.namedItem(f) as HTMLInputElement | null;
  const values = () => Object.fromEntries(
    [...FIELDS, 'lang' as const].map((k) => [k, control(k as Field)?.value ?? '']),
  );

  function setError(f: Field, key: string | undefined): void {
    const input = control(f);
    const out = document.getElementById(f + '-error');
    if (!input || !out) return;
    out.textContent = key ? msg(key) : '';
    if (key) input.setAttribute('aria-invalid', 'true');
    else input.removeAttribute('aria-invalid');
  }

  function check(): Partial<Record<string, LeadError>> {
    const r = validateLead(values());
    return r.ok ? {} : r.errors;
  }

  function showErrors(errors: Partial<Record<string, string>>): Field | null {
    let first: Field | null = null;
    for (const f of FIELDS) {
      setError(f, errors[f]);
      if (errors[f] && !first) first = f;
    }
    return first;
  }

  for (const f of FIELDS) {
    control(f)?.addEventListener('blur', () => setError(f, check()[f]));
  }

  const labels = new Map<HTMLButtonElement, string>([
    [sendBtn, sendBtn.textContent ?? ''],
    [waBtn, waBtn.textContent ?? ''],
  ]);

  function busy(btn: HTMLButtonElement | null, on: boolean): void {
    for (const b of [sendBtn, waBtn]) {
      b.disabled = on;
      b.textContent = on && b === btn ? form.getAttribute('data-sending') ?? '' : labels.get(b) ?? '';
    }
  }

  function showBoxError(key: string): void {
    errText.textContent = msg(key);
    errBox.hidden = false;
  }

  function summary(): string {
    const v = values();
    return [v.name, v.phone, v.businessName, v.message].filter(Boolean).join(' - ');
  }

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    errBox.hidden = true;
    const first = showErrors(check());
    if (first) {
      control(first)?.focus();
      return;
    }
    const btn = (e as SubmitEvent).submitter as HTMLButtonElement | null;
    const wantsWa = btn === waBtn;
    busy(btn, true);
    // iOS blocks pop-ups opened after an await, so open the tab on the click itself.
    const waWin = wantsWa ? window.open('about:blank', '_blank') : null;
    if (waWin) waWin.opener = null;
    try {
      const res = await fetch('/api/demo', {
        method: 'POST',
        body: new FormData(form),
        headers: { accept: 'application/json' },
      });
      if (res.status === 200) {
        form.hidden = true;
        success.hidden = false;
        success.focus();
        if (waWin) waWin.location.href = waLink(summary());
        return;
      }
      waWin?.close();
      // Turnstile tokens are single-use: any failed attempt needs a fresh one.
      (window as any).turnstile?.reset?.();
      if (res.status === 422) {
        const body = await res.json().catch(() => ({}));
        const first422 = showErrors((body && body.errors) || {});
        if (first422) control(first422)?.focus();
        else showBoxError('errorServer');
      } else if (res.status === 429) {
        showBoxError('errorRate');
      } else if (res.status === 403) {
        showBoxError('errorChallenge');
      } else {
        showBoxError('errorNetwork');
      }
    } catch {
      waWin?.close();
      (window as any).turnstile?.reset?.();
      showBoxError('errorNetwork');
    }
    busy(null, false);
  });

  // Keep wa link in the error box prefilled in the page language.
  const link = errBox.querySelector<HTMLAnchorElement>('a');
  if (link) link.href = waLink(lang === 'ar' ? 'مرحبا، أرغب بحجز عرض تجريبي' : 'Hello, I would like to book a demo');
}

const formEl = document.getElementById('demo-form');
if (formEl instanceof HTMLFormElement) init(formEl);
