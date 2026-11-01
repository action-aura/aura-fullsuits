// GET /api/account/downloads/fetch?t=<token>&l=<ar|en>
// Redeems a link made by /downloads/authorize and STREAMS the installer from Owner to the browser. Nothing is
// buffered (installers are ~250 MB, a Pages Function has little memory) and nothing is stored here.
// No session cookie is required on purpose: the link is the authorisation (it is bound to the account on the
// Owner side, lives 10 minutes, works 3 times), so a phone can open a link copied from the laptop.
import type { Env } from '../../../env';
import { accountsEnabled, clientIp, DOWNLOAD_TOKEN_RE, notFound } from '../../../_lib/account-gate';
import { callOwnerStream, ownerConfig } from '../../../_lib/owner-account';
import { safeDownloadHeaders } from '../../../_lib/download-headers';

export const onRequest = async (ctx: { request: Request; env: Env }): Promise<Response> => {
  const { request, env } = ctx;
  if (!accountsEnabled(env) || request.method !== 'GET') return notFound();
  const url = new URL(request.url);
  const lang = url.searchParams.get('l') === 'ar' ? 'ar' : 'en';
  const back = (reason: string): Response =>
    new Response(null, { status: 303, headers: { location: `/${lang}/account/downloads/?e=${reason}`, 'cache-control': 'no-store' } });

  const token = url.searchParams.get('t') ?? '';
  if (!DOWNLOAD_TOKEN_RE.test(token)) return back('expired');
  const ip = clientIp(request, env);
  if (!ip || !ownerConfig(env)) return back('unavailable');

  const upstream = await callOwnerStream(env, 'downloads/fetch', { token, client_ip: ip });
  if (!upstream) return back('unavailable');
  if (upstream.status === 404 || upstream.status === 410 || upstream.status === 401 || upstream.status === 400) {
    await upstream.body?.cancel();
    return back('expired');
  }
  if (!upstream.ok || !upstream.body) {
    await upstream.body?.cancel();
    return back('unavailable');
  }
  return new Response(upstream.body, { status: 200, headers: safeDownloadHeaders(upstream.headers) });
};
