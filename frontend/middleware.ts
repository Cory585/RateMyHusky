// Vercel Routing Middleware: maintenance gate + authenticated API proxy.
//
// - While maintenance.config.json has "on": true (stamped by maintenance.py),
//   every request without a valid signed bypass cookie is 307'd to
//   /maintenance.html — same semantics the page's auto-recovery poll and the
//   SPA's maintenanceGuard() rely on.
// - Preview deploys (VERCEL_ENV=preview) are always gated the same way, so
//   only bypass holders can see them; the page shows dev-preview copy there.
// - /bypass-handoff + /bypass-redeem copy a ratemyhusky.com bypass cookie
//   over to a preview deploy, which can't read it across domains (see the
//   handlers below).
// - /api/* is proxied to Railway with the x-proxy-key header so the backend
//   can reject traffic that didn't come through Vercel.
//
// Env vars (set in Vercel project settings):
//   RAILWAY_ORIGIN    — backend origin, e.g. https://xyz.up.railway.app
//   MAINT_SIGNING_KEY — HMAC key for bypass tokens (mint: maintenance.py -invite)
//   PROXY_SECRET      — shared secret the backend checks; also set on Railway

import maintenance from './maintenance.config.json';

// Skip the maintenance page itself, the assets it needs, crawler/AdSense
// files, Vercel internals (analytics beacons, etc.), and professor photo
// thumbnails (a catalog page loads dozens; each would be a middleware call).
export const config = {
  matcher:
    '/((?!maintenance\\.html|logo\\.jpg|neu-husky-icon\\.png|robots\\.txt|ads\\.txt|sitemap\\.xml|llms\\.txt|2c426a030622417e4b03f2011ae5959e\\.txt|_vercel|photos/).*)',
};

// Preview hosts /bypass-handoff will redirect to. Not the security boundary
// (anyone can claim a free *.vercel.app alias): handoff codes are bound to
// their target origin and only redeemable there, so a code sent to a
// look-alike host is useless.
const PREVIEW_ORIGIN_RE = /^https:\/\/rate-my-husky-(?:git-[a-z0-9-]+|[a-z0-9]+)-grain-is-grainys-projects\.vercel\.app$/;
const HANDOFF_TTL_SECONDS = 60;

async function hmacHex(key: string, message: string): Promise<string> {
  const enc = new TextEncoder();
  const cryptoKey = await crypto.subtle.importKey(
    'raw', enc.encode(key), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign'],
  );
  const mac = await crypto.subtle.sign('HMAC', cryptoKey, enc.encode(message));
  return Array.from(new Uint8Array(mac), b => b.toString(16).padStart(2, '0')).join('');
}

function safeEqual(expected: string, actual: string): boolean {
  if (expected.length !== actual.length) return false;
  let diff = 0;
  for (let i = 0; i < expected.length; i++) {
    diff |= expected.charCodeAt(i) ^ actual.charCodeAt(i);
  }
  return diff === 0;
}

function isFuture(epochSeconds: string): boolean {
  return /^\d+$/.test(epochSeconds) && Number(epochSeconds) >= Date.now() / 1000;
}

// Bypass token: "<name>.<expiry-epoch-seconds>.<hex hmac-sha256 of "name.expiry">"
// Returns [name, expiry] when the request carries a valid one.
async function validBypass(request: Request): Promise<[string, string] | null> {
  const key = process.env.MAINT_SIGNING_KEY;
  if (!key) return null;

  const cookies = request.headers.get('cookie') || '';
  const match = cookies.match(/(?:^|;\s*)rmh_dev=([^;]+)/);
  if (!match) return null;

  const parts = match[1].split('.');
  if (parts.length !== 3) return null;
  const [name, expStr, sig] = parts;
  if (!isFuture(expStr)) return null;

  return safeEqual(await hmacHex(key, `${name}.${expStr}`), sig) ? [name, expStr] : null;
}

// Handoff code: "<name>.<expiry>.<code-expiry>.<hmac of handoff|origin|name.expiry|code-expiry>".
// Carries no usable bypass signature, so leaking one leaks nothing.
async function mintHandoffCode(key: string, origin: string, name: string, exp: string): Promise<string> {
  const codeExp = String(Math.floor(Date.now() / 1000) + HANDOFF_TTL_SECONDS);
  const sig = await hmacHex(key, `handoff|${origin}|${name}.${exp}|${codeExp}`);
  return `${name}.${exp}.${codeExp}.${sig}`;
}

// Returns the bypass token to set as this origin's cookie, or null.
async function redeemHandoffCode(key: string, origin: string, code: string): Promise<string | null> {
  const parts = code.split('.');
  if (parts.length !== 4) return null;
  const [name, exp, codeExp, sig] = parts;
  if (!isFuture(exp) || !isFuture(codeExp)) return null;
  if (!safeEqual(await hmacHex(key, `handoff|${origin}|${name}.${exp}|${codeExp}`), sig)) return null;
  return `${name}.${exp}.${await hmacHex(key, `${name}.${exp}`)}`;
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
  // Railway appends the Vercel edge's own address to X-Forwarded-For, so the
  // last entry is Vercel, not the student. Vercel overwrites x-real-ip, so
  // pass it on as the one client IP the backend trusts; drop any client copy.
  headers.delete('x-rmh-client-ip');
  const clientIp = request.headers.get('x-real-ip');
  if (clientIp) headers.set('x-rmh-client-ip', clientIp);

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
  // Search & AI crawlers
  'googlebot', 'google-inspectiontool', 'google-extended', 'bingbot', 'gptbot', 'oai-searchbot',
  'chatgpt-user', 'claudebot', 'claude-searchbot', 'claude-user',
  'anthropic-ai', 'perplexitybot', 'perplexity-user', 'applebot',
  // Social link-preview scrapers (none run JS, so they need the snapshot
  // to get a rich preview with title/description/image)
  'facebookexternalhit', 'facebookcatalog', 'twitterbot', 'slackbot',
  'slack-imgproxy', 'discordbot', 'linkedinbot', 'whatsapp', 'telegrambot',
  'pinterest', 'redditbot', 'skypeuripreview', 'vkshare', 'embedly',
  'iframely', 'mastodon', 'bsky',
  // Google AI fetcher + AdSense content crawlers (none run JS)
  'googleother', 'google-cloudvertexbot', 'mediapartners-google', 'adsbot-google',
];

function isCrawler(ua: string): boolean {
  const lc = ua.toLowerCase();
  return CRAWLER_UAS.some((bot) => lc.includes(bot));
}

// Matches /professors/<slug>, /courses/<code>, and /departments/<slug> (single trailing segment).
const DETAIL_RE = /^\/(professors|courses|departments)\/([^/]+)\/?$/;

// Exact non-detail paths that get a server-rendered snapshot for crawlers.
const STATIC_RENDER: Record<string, string> = {
  '/': '/render/home',
  '/professors': '/render/professors',
  '/courses': '/render/courses',
  '/departments': '/render/departments',
};

async function fetchSnapshot(pathname: string): Promise<Response | undefined> {
  const origin = process.env.RAILWAY_ORIGIN;
  if (!origin) return undefined;

  // Exact-match home/listing routes (no slug → no SSRF decoding needed).
  const staticTarget = STATIC_RENDER[pathname];
  if (staticTarget) {
    const headers = new Headers();
    const proxyKey = process.env.PROXY_SECRET;
    if (proxyKey) headers.set('x-proxy-key', proxyKey);
    try {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 4000);
      const res = await fetch(new URL(staticTarget, origin), {
        headers, signal: controller.signal,
      });
      clearTimeout(timer);
      if (!res.ok && res.status !== 404) return undefined;
      return res;
    } catch {
      return undefined;
    }
  }

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
  const kind = m[1] === 'professors' ? 'professors' : m[1] === 'courses' ? 'courses' : 'departments';
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

  // Both handoff routes run before the maintenance gate so they work while
  // the site is down too.
  const key = process.env.MAINT_SIGNING_KEY;

  // A preview's gate page sends the browser here (on ratemyhusky.com) with
  // ?to=<preview origin>. If it already holds a valid bypass cookie, bounce
  // back to that preview's /bypass-redeem with a short-lived code bound to
  // it; otherwise bounce back to its gate page with handoff=none.
  if (url.pathname === '/bypass-handoff') {
    const to = url.searchParams.get('to') || '';
    if (!PREVIEW_ORIGIN_RE.test(to)) {
      return new Response('Invalid handoff target', { status: 400 });
    }
    const bypass = await validBypass(request);
    const location = key && bypass
      ? `${to}/bypass-redeem?code=${await mintHandoffCode(key, to, ...bypass)}`
      : `${to}/maintenance.html?handoff=none`;
    return new Response(null, {
      status: 302,
      headers: { Location: location, 'cache-control': 'no-store' },
    });
  }

  // On the preview: trade a handoff code minted for this origin for this
  // origin's own bypass cookie.
  if (url.pathname === '/bypass-redeem') {
    const token = key && await redeemHandoffCode(key, url.origin, url.searchParams.get('code') || '');
    const headers = new Headers({ 'cache-control': 'no-store' });
    if (token) {
      headers.set('Location', '/');
      headers.set('Set-Cookie', `rmh_dev=${token}; Path=/; Max-Age=2592000; SameSite=Lax; Secure`);
    } else {
      headers.set('Location', '/maintenance.html?handoff=none');
    }
    return new Response(null, { status: 302, headers });
  }

  // Feedback stays open during maintenance (the page has its own form);
  // it's Turnstile-verified and rate-limited server-side.
  const isFeedback = url.pathname === '/api/feedback';

  const gated = maintenance.on || process.env.VERCEL_ENV === 'preview';

  if (gated && !isFeedback && !(await validBypass(request))) {
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
    (DETAIL_RE.test(url.pathname) || url.pathname in STATIC_RENDER)
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
