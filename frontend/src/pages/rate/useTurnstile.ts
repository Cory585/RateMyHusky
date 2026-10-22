import { useCallback, useEffect, useRef, useState } from 'react';
import type { RefObject } from 'react';

const TURNSTILE_SITE_KEY = import.meta.env.VITE_TURNSTILE_SITE_KEY;
// Without a site key there's no widget to wait for; the backend skips the check when it has no secret either.
const NO_WIDGET_TOKEN = 'no-turnstile';

export function useTurnstile(): { containerRef: RefObject<HTMLDivElement | null>; token: string; failed: boolean; reset: () => void } {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const widgetIdRef = useRef<string | null>(null);
  const [token, setToken] = useState('');
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (!TURNSTILE_SITE_KEY) return;
    // Wait for the Turnstile script to load, as FeedbackTab does.
    const interval = setInterval(() => {
      if (!window.turnstile || !containerRef.current) return;
      clearInterval(interval);
      const id = window.turnstile.render(containerRef.current, {
        sitekey: TURNSTILE_SITE_KEY,
        // Visible so the checkpoint shows Cloudflare's box; Rate hides it again once a token is in hand.
        appearance: 'always',
        callback: (t: string) => {
          setToken(t);
          setFailed(false); // an automatic retry recovered from an earlier error
        },
        'error-callback': () => setFailed(true),
        'expired-callback': () => {
          setToken('');
          window.turnstile?.reset(id);
        },
        theme: document.documentElement.classList.contains('dark') ? 'dark' : 'light',
      });
      widgetIdRef.current = id;
    }, 100);
    return () => {
      clearInterval(interval);
      if (widgetIdRef.current !== null && window.turnstile) {
        window.turnstile.remove(widgetIdRef.current);
        widgetIdRef.current = null;
      }
      setToken('');
    };
  }, []);

  const reset = useCallback(() => {
    setToken('');
    setFailed(false);
    if (widgetIdRef.current !== null) window.turnstile?.reset(widgetIdRef.current);
  }, []);

  return { containerRef, token: TURNSTILE_SITE_KEY ? token : NO_WIDGET_TOKEN, failed, reset };
}
