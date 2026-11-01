import { strings } from './api';
import { $, reveal, showMessage } from './ui';
import { loadSummary } from './summary';
import { renderDownloads } from './download-buttons';

const t = strings();
const why = new URLSearchParams(location.search).get('e');
if (why === 'expired') showMessage(t.downloads.expired);
else if (why === 'unavailable') showMessage(t.downloads.unavailable);

void loadSummary(t).then((s) => {
  if (!s) return;
  reveal();
  const list = $('dl-list');
  if (s.downloads.length === 0) {
    showMessage(t.downloads.none);
    list.hidden = true;
    return;
  }
  renderDownloads(list, s.downloads, t, true);
});
