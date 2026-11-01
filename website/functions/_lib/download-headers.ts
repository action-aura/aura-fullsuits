// Headers for a streamed installer. Only these may reach the visitor as the file's type. Anything else is served
// as an opaque download, so a compromised or confused upstream cannot make the browser render HTML on our origin.
const SAFE_TYPES = new Set([
  'application/vnd.android.package-archive',
  'application/octet-stream',
  'application/x-msdownload',
  'application/vnd.microsoft.portable-executable',
  'application/zip',
  'application/x-msi',
]);
const SAFE_DISPOSITION = /^attachment; filename="[A-Za-z0-9._ ()-]{1,120}"$/;

export function safeDownloadHeaders(upstream: Headers): Headers {
  const h = new Headers({ 'cache-control': 'no-store', 'x-content-type-options': 'nosniff' });
  const type = (upstream.get('content-type') ?? '').split(';')[0].trim().toLowerCase();
  h.set('content-type', SAFE_TYPES.has(type) ? type : 'application/octet-stream');
  const disp = upstream.get('content-disposition') ?? '';
  h.set('content-disposition', SAFE_DISPOSITION.test(disp) ? disp : 'attachment');
  const len = upstream.get('content-length');
  if (len && /^\d{1,12}$/.test(len)) h.set('content-length', len);
  return h;
}
