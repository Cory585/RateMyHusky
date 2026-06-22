// Vercel Routing Middleware: maintenance gate + authenticated API proxy.
//
// - While maintenance.config.json has "on": true (stamped by maintenance.py),
//   every request without a valid signed bypass cookie is 307'd to
//   /maintenance.html — same semantics the page's auto-recovery poll and the
//   SPA's maintenanceGuard() rely on.
// - /api/* is proxied to Railway with the x-proxy-key header so the backend
//   can reject traffic that didn't come through Vercel.
//
// Env vars (set in Vercel project settings):
//   RAILWAY_ORIGIN    — backend origin, e.g. https://xyz.up.railway.app
//   MAINT_SIGNING_KEY — HMAC key for bypass tokens (mint: maintenance.py -invite)
//   PROXY_SECRET      — shared secret the backend checks; also set on Railway

import maintenance from './maintenance.config.json';

// Skip the maintenance page itself, the assets it needs, crawler/AdSense
// files, and Vercel internals (analytics beacons, etc.).
export const config = {
  matcher:
    '/((?!maintenance\\.html|logo\\.jpg|neu-husky-icon\\.png|robots\\.txt|ads\\.txt|_vercel).*)',
};

// Bypass token: "<name>.<expiry-epoch-seconds>.<hex hmac-sha256 of "name.expiry">"
async function hasValidBypass(request: Request): Promise<boolean> {
  const key = process.env.MAINT_SIGNING_KEY;
  if (!key) return false;

  const cookies = request.headers.get('cookie') || '';
  const match = cookies.match(/(?:^|;\s*)rmh_dev=([^;]+)/);
  if (!match) return false;

  const parts = match[1].split('.');
  if (parts.length !== 3) return false;
  const [name, expStr, sig] = parts;
  if (!/^\d+$/.test(expStr) || Number(expStr) < Date.now() / 1000) return false;

  const enc = new TextEncoder();
  const cryptoKey = await crypto.subtle.importKey(
    'raw', enc.encode(key), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign'],
  );
  const mac = await crypto.subtle.sign('HMAC', cryptoKey, enc.encode(`${name}.${expStr}`));
  const expected = Array.from(new Uint8Array(mac), b => b.toString(16).padStart(2, '0')).join('');

  if (expected.length !== sig.length) return false;
  let diff = 0;
  for (let i = 0; i < expected.length; i++) {
    diff |= expected.charCodeAt(i) ^ sig.charCodeAt(i);
  }
  return diff === 0;
}

async function proxyToBackend(request: Request, url: URL): Promise<Response> {
  const origin = process.env.RAILWAY_ORIGIN;
  if (!origin) {
    return new Response(
      JSON.stringify({ error: 'RAILWAY_ORIGIN is not configured' }),
      { status: 503, headers: { 'content-type': 'application/json' } },
    );
  }
  const target = new URL(url.pathname + url.search, origin);

  const headers = new Headers(request.headers);
  headers.delete('host');
  headers.delete('content-length');
  const proxyKey = process.env.PROXY_SECRET;
  if (proxyKey) headers.set('x-proxy-key', proxyKey);

  const hasBody = request.method !== 'GET' && request.method !== 'HEAD';
  return fetch(target, {
    method: request.method,
    headers,
    body: hasBody ? await request.arrayBuffer() : undefined,
    // Pass 3xx (e.g. the Google OAuth redirect) through to the browser.
    redirect: 'manual',
  });
}

const CRAWLER_UAS = [
  'googlebot', 'google-extended', 'bingbot', 'gptbot', 'oai-searchbot',
  'chatgpt-user', 'claudebot', 'claude-searchbot', 'claude-user',
  'anthropic-ai', 'perplexitybot', 'perplexity-user', 'applebot',
];

function isCrawler(ua: string): boolean {
  const lc = ua.toLowerCase();
  return CRAWLER_UAS.some((bot) => lc.includes(bot));
}

// Matches /professors/<slug> and /courses/<code> (single trailing segment).
const DETAIL_RE = /^\/(professors|courses)\/([^/]+)\/?$/;

async function fetchSnapshot(pathname: string): Promise<Response | undefined> {
  const origin = process.env.RAILWAY_ORIGIN;
  if (!origin) return undefined;
  const m = pathname.match(DETAIL_RE);
  if (!m) return undefined;
  const slug = m[2];
  // Reject encoded slashes / traversal so the slug can't escape the
  // /render/<kind>/ path segment (edge-to-origin SSRF hardening).
  let decoded = slug;
  try {
    decoded = decodeURIComponent(slug);
    // one more pass to catch double-encoding
    decoded = decodeURIComponent(decoded);
  } catch {
    return undefined; // malformed percent-encoding
  }
  if (/[/\\]/.test(decoded) || decoded.includes('..')) {
    return undefined;
  }
  const kind = m[1] === 'professors' ? 'professors' : 'courses';
  const target = new URL(`/render/${kind}/${slug}`, origin);
  const headers = new Headers();
  const proxyKey = process.env.PROXY_SECRET;
  if (proxyKey) headers.set('x-proxy-key', proxyKey);
  try {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 4000);
    const res = await fetch(target, { headers, signal: controller.signal });
    clearTimeout(timer);
    if (!res.ok && res.status !== 404) return undefined; // fall back to SPA on 5xx
    return res;
  } catch {
    return undefined; // any failure → SPA fallback
  }
}

export default async function middleware(request: Request): Promise<Response | undefined> {
  const url = new URL(request.url);

  // Feedback stays open during maintenance (the page has its own form);
  // it's Turnstile-verified and rate-limited server-side.
  const isFeedback = url.pathname === '/api/feedback';

  if (maintenance.on && !isFeedback && !(await hasValidBypass(request))) {
    return new Response(null, {
      status: 307,
      headers: { Location: new URL('/maintenance.html', request.url).toString() },
    });
  }

  if (url.pathname.startsWith('/api/')) {
    return proxyToBackend(request, url);
  }

  if (
    process.env.PRERENDER_ENABLED === 'true' &&
    isCrawler(request.headers.get('user-agent') || '') &&
    DETAIL_RE.test(url.pathname)
  ) {
    const snapshot = await fetchSnapshot(url.pathname);
    if (snapshot) {
      return new Response(snapshot.body, {
        status: snapshot.status,
        headers: {
          'content-type': 'text/html; charset=utf-8',
          'cache-control': snapshot.headers.get('cache-control') ||
            'public, max-age=3600, s-maxage=86400',
          'x-prerendered': '1',
        },
      });
    }
    // snapshot undefined → fall through to SPA below
  }

  // Fall through to vercel.json rewrites (SPA fallback).
  return undefined;
}
