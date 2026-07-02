// Runnable self-check for askPinMatch (no unit-test framework is installed).
// Run: cd frontend && npx tsx src/utils/askPinMatch.selftest.ts
// Prints PASS/FAIL per check and exits non-zero on any failure.
import { argv } from 'node:process';
import { fileURLToPath } from 'node:url';
import { matchesPin, isPinned, pinnedFirst } from './askPinMatch';

function selftest(): number {
  const fails: string[] = [];
  const check = (label: string, cond: boolean) => {
    if (!cond) fails.push(label);
    console.log((cond ? 'PASS' : 'FAIL') + ': ' + label);
  };

  const longBody = 'Took CS3500 with him. Hard but fair, great office hours, learned a ton about systems design and testing methodology over the whole semester.';
  const snippet200 = longBody.slice(0, 130); // stand-in for a 200-char prefix shorter than body

  check('exact match', matchesPin('hard but fair', 'hard but fair'));
  check('prefix match (snippet is prefix of longer body)', matchesPin(longBody, snippet200));
  check('whitespace-difference match', matchesPin('hard   but\n fair', 'hard but fair'));
  check('body shorter than snippet still matches when body is a prefix', matchesPin('hard but', 'hard but fair'));
  check('non-match', !matchesPin('totally unrelated comment', 'hard but fair'));
  check('empty snippet never matches', !matchesPin('hard but fair', ''));
  check('empty body never matches', !matchesPin('', 'hard but fair'));

  const items = [{ b: 'zeta review' }, { b: longBody }, { b: 'alpha review' }];
  const out = pinnedFirst(items, (x) => x.b, [snippet200]);
  check('pinnedFirst hoists match to front', out[0].b === longBody);
  check('pinnedFirst preserves rest order', out[1].b === 'zeta review' && out[2].b === 'alpha review');
  check('pinnedFirst no-op on empty snippets', pinnedFirst(items, (x) => x.b, []) === items);

  check('isPinned true on any match', isPinned(longBody, ['nope', snippet200]));
  check('isPinned false when none match', !isPinned('nope', ['other', 'thing']));

  // Regression: evidence-table snippet keeps HTML entities; page body is html.unescape'd.
  check('html-entity snippet matches unescaped body (&amp; / apostrophe)',
    matchesPin("Rowe & the TA were great, don't skip", 'Rowe &amp; the TA were great, don&#39;t skip'));
  // Regression: NFKC ligature/superscript normalizes on both sides.
  check('NFKC ligature/superscript match', matchesPin('final exam x2 was difficult', 'ﬁnal exam x² was diﬃcult') || matchesPin('ﬁnal exam x² was diﬃcult', 'final exam x2 was difficult'));

  console.log(fails.length ? `${fails.length} FAIL(s): ` + fails.join(', ') : 'ALL PASS');
  return fails.length ? 1 : 0;
}

if (argv[1] && fileURLToPath(import.meta.url) === argv[1]) {
  process.exit(selftest());
}
