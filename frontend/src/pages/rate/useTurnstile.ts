import { useCallback, useEffect, useRef, useState } from 'react';
import type { RefObject } from 'react';

const TURNSTILE_SITE_KEY = import.meta.env.VITE_TURNSTILE_SITE_KEY;

export function useTurnstile(): { containerRef: RefObject<HTMLDivElement | null>; token: string; failed: boolean; reset: () => void } {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const widgetIdRef = useRef<string | null>(null);
  const [token, setToken] = useState('');
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    // Wait for the Turnstile script to load, as FeedbackTab does.
    const interval = setInterval(() => {
      if (!window.turnstile || !containerRef.current || !TURNSTILE_SITE_KEY) return;
      clearInterval(interval);
      const id = window.turnstile.render(containerRef.current, {
        sitekey: TURNSTILE_SITE_KEY,
        appearance: 'interaction-only',
        callback: (t: string) => setToken(t),
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

  return { containerRef, token, failed, reset };
}
