import searchFixture from './fixtures/search.json';

// Flip once the site is out of maintenance and /api/* returns JSON again.
export const USE_FIXTURES = true;

// Must be the site, not the backend host: the API only accepts traffic
// proxied through here, which is what attaches the shared secret.
const API_BASE = 'https://ratemyhusky.com';

export type ProfessorSuggestion = {
  type: 'professor';
  name: string;
  dept: string | null;
  rating: number | null;
  slug: string;
};

export type CourseSuggestion = {
  type: 'course';
  code: string;
  name: string | null;
  dept: string | null;
};

export type Suggestion = ProfessorSuggestion | CourseSuggestion;

/** The site is down: maintenance, a 503, or no network. Anything else — a 403
 *  from an origin that isn't allowlisted, a 429, a 500 — is a real error and
 *  must not be reported as maintenance. */
class Unavailable extends Error {}

async function get<T>(path: string): Promise<T> {
  let resp: Response;
  try {
    resp = await fetch(`${API_BASE}${path}`, { credentials: 'omit' });
  } catch (err) {
    throw new Unavailable(`network error: ${(err as Error).message}`);
  }

  if (!resp.ok) {
    const detail = `${resp.status} ${resp.statusText}`;
    if (resp.status === 503) throw new Unavailable(detail);
    throw new Error(detail);
  }

  const text = await resp.text();
  try {
    return JSON.parse(text) as T;
  } catch {
    // The maintenance redirect lands here: a 200 carrying HTML, not JSON.
    throw new Unavailable('expected JSON, got a page');
  }
}

export async function searchProfessors(
  query: string,
  limit = 5,
): Promise<ProfessorSuggestion[]> {
  const q = query.trim();
  if (q.length < 2) return [];

  if (USE_FIXTURES) {
    const needle = q.toLowerCase();
    return (searchFixture as Suggestion[])
      .filter((s): s is ProfessorSuggestion => s.type === 'professor')
      .filter((s) => s.name.toLowerCase().includes(needle))
      .slice(0, limit);
  }

  return get<ProfessorSuggestion[]>(
    `/api/search?q=${encodeURIComponent(q)}&type=Professor&limit=${limit}`,
  );
}

export function isUnavailable(err: unknown): boolean {
  return err instanceof Unavailable;
}

export function professorUrl(slug: string): string {
  return `${API_BASE}/professors/${encodeURIComponent(slug)}`;
}
