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

/** A maintenance redirect arrives as HTML, so a parse failure is expected. */
class Unavailable extends Error {}

async function get<T>(path: string): Promise<T> {
  const resp = await fetch(`${API_BASE}${path}`, { credentials: 'omit' });
  if (!resp.ok) throw new Unavailable(`${resp.status} ${resp.statusText}`);
  const text = await resp.text();
  try {
    return JSON.parse(text) as T;
  } catch {
    throw new Unavailable('expected JSON, got a page — site may be in maintenance');
  }
}

export async function searchProfessors(query: string, limit = 5): Promise<Suggestion[]> {
  const q = query.trim();
  if (q.length < 2) return [];

  if (USE_FIXTURES) {
    const all = searchFixture as Suggestion[];
    const needle = q.toLowerCase();
    return all
      .filter((s) => (s.type === 'professor' ? s.name : s.code).toLowerCase().includes(needle))
      .slice(0, limit);
  }

  return get<Suggestion[]>(
    `/api/search?q=${encodeURIComponent(q)}&type=Professor&limit=${limit}`,
  );
}

export function isUnavailable(err: unknown): boolean {
  return err instanceof Unavailable;
}

export function professorUrl(slug: string): string {
  return `${API_BASE}/professors/${slug}`;
}
