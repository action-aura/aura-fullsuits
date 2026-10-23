// Product cards (home section 8). A tap on a greyed card toggles .peek, which lifts its taped note a few pixels (CSS,
// transform only). Hover and keyboard focus do the same in CSS alone, so this only exists for touch. A tap on a
// link inside the card is left alone.
const cards = document.querySelectorAll<HTMLElement>('[data-pr-card]');
cards.forEach((card) => {
  card.addEventListener('click', (e) => {
    if ((e.target as Element).closest('a')) return;
    card.classList.toggle('peek');
  });
});
