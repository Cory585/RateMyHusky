// Persists the current Ask question + answer across navigation so a clicked citation can be
// revisited via the "← Ask" breadcrumb. Lives in sessionStorage: survives route changes but is
// dropped on a fresh tab/refresh (the homepage clears it on plain load; only the breadcrumb
// restores it). Keyed by askedAt so the Professor page's pin logic stays consistent.
import type { ChatResponse } from '../api/api';

export const ASK_SESSION_KEY = 'ask_session';

export interface AskSession {
  query: string;
  result: ChatResponse;
  askedAt: number;
}

export function saveAskSession(query: string, result: ChatResponse, askedAt: number): void {
  try {
    sessionStorage.setItem(ASK_SESSION_KEY, JSON.stringify({ query, result, askedAt }));
  } catch { /* storage full / disabled — non-critical */ }
}

export function loadAskSession(): AskSession | null {
  try {
    const raw = sessionStorage.getItem(ASK_SESSION_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as AskSession;
    if (!parsed || typeof parsed.query !== 'string' || !parsed.result) return null;
    return parsed;
  } catch { return null; }
}

export function clearAskSession(): void {
  try { sessionStorage.removeItem(ASK_SESSION_KEY); } catch { /* non-critical */ }
}
