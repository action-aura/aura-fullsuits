// Small DOM helpers. Everything the server sends is put in with textContent, never innerHTML: a hostile or odd
// value (a branch label, a customer name) can only ever be text.
export function $(id: string): HTMLElement {
  const node = document.getElementById(id);
  if (!node) throw new Error(`missing #${id}`);
  return node;
}

export function el<K extends keyof HTMLElementTagNameMap>(tag: K, className?: string, text?: string): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

export function showMessage(text: string, kind: 'error' | 'ok' = 'error'): void {
  const box = $('acct-msg');
  box.textContent = text;
  box.dataset.kind = kind;
  box.hidden = false;
  if (kind === 'error') box.setAttribute('role', 'alert');
  else box.setAttribute('role', 'status');
}

export function clearMessage(): void {
  $('acct-msg').hidden = true;
}

export function setBusy(button: HTMLButtonElement, busy: boolean, busyLabel: string): void {
  button.disabled = busy;
  button.textContent = busy ? busyLabel : button.dataset.label ?? button.textContent ?? '';
}

export function reveal(): void {
  const loading = document.getElementById('acct-loading');
  if (loading) loading.hidden = true;
  $('acct-root').hidden = false;
}

/** Digits and a leading + only: safe to put in a tel: or wa.me link. */
export function phoneDigits(raw: string): string {
  const cleaned = raw.replace(/[^\d+]/g, '');
  return cleaned.startsWith('+') ? '+' + cleaned.slice(1).replace(/\+/g, '') : cleaned.replace(/\+/g, '');
}
