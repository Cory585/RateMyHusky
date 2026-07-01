// Runnable self-check for askSession (no unit-test framework is installed).
// Run: cd frontend && npx tsx src/utils/askSession.selftest.ts
// Prints PASS/FAIL per check and exits non-zero on any failure.
import { argv } from 'node:process';
import { fileURLToPath } from 'node:url';
import { saveAskSession, loadAskSession, clearAskSession, ASK_SESSION_KEY } from './askSession';
import type { ChatResponse } from '../api/api';

// Minimal in-memory sessionStorage stand-in (Node has no DOM Storage).
function installStorage(): Record<string, string> {
  const store: Record<string, string> = {};
  (globalThis as unknown as { sessionStorage: Storage }).sessionStorage = {
    getItem: (k: string) => (k in store ? store[k] : null),
    setItem: (k: string, v: string) => { store[k] = String(v); },
    removeItem: (k: string) => { delete store[k]; },
    clear: () => { for (const k of Object.keys(store)) delete store[k]; },
    key: (i: number) => Object.keys(store)[i] ?? null,
    get length() { return Object.keys(store).length; },
  };
  return store;
}

function selftest(): number {
  const fails: string[] = [];
  const check = (label: string, cond: boolean) => {
    if (!cond) fails.push(label);
    console.log((cond ? 'PASS' : 'FAIL') + ': ' + label);
  };

  const store = installStorage();
  const result: ChatResponse = {
    mode: 'question', answer: 'He is fair. [1]', sources: [], professor_slug: 'ada', course_code: null, disclaimer: 'AI-generated.',
  };

  check('loadAskSession returns null when nothing saved', loadAskSession() === null);

  saveAskSession('is ada fair?', result, 123);
  check('saveAskSession writes to the known key', ASK_SESSION_KEY in store);

  const loaded = loadAskSession();
  check('loadAskSession round-trips the query', loaded?.query === 'is ada fair?');
  check('loadAskSession round-trips askedAt', loaded?.askedAt === 123);
  check('loadAskSession round-trips the result', loaded?.result.mode === 'question'
    && (loaded.result as { answer: string }).answer === 'He is fair. [1]');

  clearAskSession();
  check('clearAskSession removes the entry', loadAskSession() === null);
  check('clearAskSession removed the raw key', !(ASK_SESSION_KEY in store));

  // Corrupt JSON must not throw — treated as no session.
  store[ASK_SESSION_KEY] = '{not valid json';
  check('loadAskSession returns null on corrupt data (no throw)', loadAskSession() === null);

  console.log(fails.length ? `${fails.length} FAIL(s): ` + fails.join(', ') : 'ALL PASS');
  return fails.length ? 1 : 0;
}

if (argv[1] && fileURLToPath(import.meta.url) === argv[1]) {
  process.exit(selftest());
}
