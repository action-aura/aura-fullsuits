// The gym check-in preview (E3). Tap a member card: a valid subscription is welcomed with its days left and counts as a
// visit; an expired one is sent back to the front desk and does NOT count. Words come from data-* attributes, so the
// script holds no language of its own.
const root = document.querySelector<HTMLElement>('[data-checkin]');
if (root) init(root);

function init(root: HTMLElement) {
  const q = <T extends HTMLElement>(s: string) => root.querySelector<T>(`[data-ck-${s}]`)!;
  const visitsEl = q('visits'), door = q('door'), msg = q('msg'), live = q('live');
  const d = root.dataset;
  const reduced = () => matchMedia('(prefers-reduced-motion: reduce)').matches;
  let visits = 0;

  function check(card: HTMLElement) {
    const valid = card.dataset.ok === 'true';
    let text: string;
    if (valid) {
      text = d.welcome!.replace('{name}', card.dataset.name!).replace('{days}', card.dataset.days!);
      visits++;
      visitsEl.textContent = String(visits);
      if (!reduced()) {
        visitsEl.classList.remove('dc-bump');
        void visitsEl.offsetWidth;
        visitsEl.classList.add('dc-bump');
      }
    } else {
      text = d.expired!;
    }
    msg.textContent = text;
    door.dataset.state = valid ? 'ok' : 'expired';
    if (!reduced()) {
      door.classList.remove('ck-flip');
      void door.offsetWidth;
      door.classList.add('ck-flip');
    }
    root.querySelectorAll('[data-member]').forEach((c) => c.setAttribute('aria-pressed', String(c === card)));
    live.textContent = text;
  }

  root.querySelectorAll<HTMLElement>('[data-member]').forEach((c) => c.addEventListener('click', () => check(c)));
}
