// Runnable self-check for rateFlow (no unit-test framework is installed).
// Run: cd frontend && npx tsx src/pages/rate/rateFlow.selftest.ts
// Prints PASS/FAIL per check and exits non-zero on any failure.
import { argv } from 'node:process';
import { fileURLToPath } from 'node:url';
import {
  answer, buildSubmission, contextChip, displayTerm, firstStep, formatCode, hoursBucket,
  initials, lastName, nextStep, prefillCourse, prevStep, progressIndex, termYears,
  withMyRating, SEASONS,
  type Answers, type RateCourse, type RateProfessor,
} from './rateFlow';
import type { ProfessorCourse, RateStart } from '../../api/api';

function selftest(): number {
  const fails: string[] = [];
  const check = (label: string, cond: boolean) => {
    if (!cond) fails.push(label);
    console.log((cond ? 'PASS' : 'FAIL') + ': ' + label);
  };
  const same = (x: unknown, y: unknown) => JSON.stringify(x) === JSON.stringify(y);

  const alex: RateProfessor = { slug: 'alex-rivera', name: 'Alex Rivera', department: 'CS' };
  const newProf: RateProfessor = { slug: null, name: 'Sam Newhire', department: null };
  const cs2500: RateCourse = { code: 'CS2500', name: 'Fundamentals of CS 1', isNew: false, terms: ['Summer 1 2025', 'Fall 2024'] };
  const noShortcuts: RateCourse = { code: 'CS3000', name: 'Algorithms', isNew: false, terms: [] };
  const newCourse: RateCourse = { code: 'CS9999', name: 'Brand New', isNew: true, terms: [] };

  // Linear first pass
  let a: Answers = { comment: '' };
  check('firstStep on empty answers is who', firstStep(a) === 'who');
  let s = nextStep('who', (a = answer(a, { professor: alex })));
  check('who -> course', s === 'course');
  s = nextStep(s, (a = answer(a, { course: cs2500 })));
  check('course -> term', s === 'term');
  s = nextStep(s, (a = answer(a, { term: 'Summer 1 2025' })));
  check('term -> overall', s === 'overall');
  s = nextStep(s, (a = answer(a, { rating: 5 })));
  check('overall -> workload', s === 'workload');
  s = nextStep(s, (a = answer(a, { hours: '5-7' })));
  check('workload -> grade', s === 'grade');
  s = nextStep(s, (a = answer(a, { grade: null })));
  check('grade -> comment', s === 'comment');
  const rePicked = answer(a, { professor: { slug: 'sam-lee', name: 'Sam Lee', department: null } });
  check('who -> course after re-picking professor', nextStep('who', rePicked) === 'course');
  check('re-pick professor, edit overall -> course', nextStep('overall', answer(rePicked, { rating: 3 })) === 'course');
  check('edit course, edit grade -> term', nextStep('grade', answer(answer(a, { course: { ...cs2500, code: 'CS3500' } }), { grade: 'A' })) === 'term');
  check('comment -> thanks', nextStep('comment', a) === 'thanks');

  // Edits return to comment / next unanswered
  check('edit grade after comment reached returns to comment', nextStep('grade', answer(a, { grade: 'A' })) === 'comment');
  const editedCourse = answer(a, { course: noShortcuts });
  check('edit course clears term', editedCourse.term === undefined && editedCourse.course?.code === 'CS3000');
  check('edit course without shortcuts goes to otherTerm', nextStep('course', editedCourse) === 'otherTerm');
  const editedCourse2 = answer(a, { course: { ...cs2500, code: 'CS2510' } });
  check('edit course with shortcuts goes to term', nextStep('course', editedCourse2) === 'term');
  const editedProf = answer(a, { professor: { ...alex, slug: 'other', name: 'Other Prof' } });
  check('edit professor clears course and term', editedProf.course === undefined && editedProf.term === undefined);
  check('edit professor keeps later answers', editedProf.rating === 5 && editedProf.hours === '5-7');
  const reselect = answer(a, { professor: { ...alex }, course: { ...cs2500 } });
  check('re-selecting same professor/course clears nothing', reselect.course?.code === 'CS2500' && reselect.term === 'Summer 1 2025');
  check('null term (Not sure) counts as answered', nextStep('course', { comment: '', professor: alex, course: cs2500, term: null }) === 'overall');

  // New professor path
  let n: Answers = answer({ comment: '' }, { professor: newProf });
  check('new professor: who -> otherCourse (skips course list)', nextStep('who', n) === 'otherCourse');
  check('new professor: newProfessor -> otherCourse', nextStep('newProfessor', n) === 'otherCourse');
  n = answer(n, { course: newCourse });
  check('otherCourse -> otherTerm for new course (no shortcuts)', nextStep('otherCourse', n) === 'otherTerm');
  check('otherTerm -> overall', nextStep('otherTerm', answer(n, { term: 'Fall 2025' })) === 'overall');
  check('prevStep otherCourse (new professor) -> newProfessor', prevStep('otherCourse', n) === 'newProfessor');
  check('prevStep otherCourse (existing professor) -> course', prevStep('otherCourse', a) === 'course');
  check('prevStep newProfessor -> who', prevStep('newProfessor', n) === 'who');

  // No-shortcuts term path
  const ns = answer(answer({ comment: '' }, { professor: alex }), { course: noShortcuts });
  check('no shortcuts: course -> otherTerm', nextStep('course', ns) === 'otherTerm');
  check('prevStep otherTerm (no shortcuts) -> course', prevStep('otherTerm', ns) === 'course');
  check('prevStep otherTerm (new professor) -> otherCourse', prevStep('otherTerm', n) === 'otherCourse');
  check('prevStep term -> course', prevStep('term', a) === 'course');
  check('prevStep overall -> term when shortcuts', prevStep('overall', a) === 'term');
  check('prevStep overall -> otherTerm when no shortcuts', prevStep('overall', ns) === 'otherTerm');
  check('prevStep who is null', prevStep('who', a) === null);
  check('prevStep comment -> grade', prevStep('comment', a) === 'grade');

  // firstStep with prefill
  check('firstStep with professor prefilled is course', firstStep({ comment: '', professor: alex }) === 'course');
  check('firstStep with professor and course prefilled is term', firstStep({ comment: '', professor: alex, course: cs2500 }) === 'term');
  check('firstStep with new professor prefilled is otherCourse', firstStep({ comment: '', professor: newProf }) === 'otherCourse');

  // prefillCourse
  const courses = [
    { code: 'CS2500', name: 'Fundamentals of CS 1', terms: ['Fall 2024'] },
  ] as ProfessorCourse[];
  check('prefillCourse unknown code is undefined', prefillCourse(courses, 'ZZ1111') === undefined);
  check('prefillCourse null code is undefined', prefillCourse(courses, null) === undefined);
  const pf = prefillCourse(courses, 'CS2500');
  check('prefillCourse known code', pf?.code === 'CS2500' && pf.isNew === false && same(pf.terms, ['Fall 2024']));

  // contextChip
  check('contextChip empty answers', contextChip({ comment: '' }) === '');
  check('contextChip professor only', contextChip({ comment: '', professor: alex }) === 'Prof. Alex Rivera');
  check('contextChip professor + course', contextChip({ comment: '', professor: alex, course: cs2500 }) === 'Rivera · CS 2500');
  check('contextChip full', contextChip(a) === 'Rivera · CS 2500 · Summer 1 2025');
  check('contextChip Not sure term', contextChip(answer(a, { term: null })) === 'Rivera · CS 2500 · Term not sure');
  check('contextChip full summer', contextChip(answer(a, { term: 'Summer 2025' })) === 'Rivera · CS 2500 · Full Summer 2025');

  // Terms
  check('displayTerm Summer 2025', displayTerm('Summer 2025') === 'Full Summer 2025');
  check('displayTerm Winter 2026', displayTerm('Winter 2026') === 'Winter 2026');
  check('displayTerm Summer 1 2025', displayTerm('Summer 1 2025') === 'Summer 1 2025');
  const full = SEASONS.find((x) => x.label === 'Full Summer');
  check('Full Summer + 2025 answers Summer 2025', full !== undefined && answer({ comment: '' }, { term: `${full.value} 2025` }).term === 'Summer 2025');
  check('SEASONS has five options', SEASONS.length === 5);
  check('termYears', same(termYears(new Date(2026, 5, 1)), [2026, 2025, 2024, 2023, 2022, 2021, 2020]));

  // Misc formatting
  check('hoursBucket 8.5', hoursBucket(8.5) === '8–10');
  check('hoursBucket 5', hoursBucket(5) === '5–7');
  check('hoursBucket 1 / 3 / 11', hoursBucket(1) === '0–2' && hoursBucket(3) === '3–4' && hoursBucket(11) === '10+');
  check('formatCode CS4973', formatCode('CS4973') === 'CS 4973');
  check('lastName / initials', lastName('Lucia Nunez') === 'Nunez' && initials('Lucia Nunez') === 'LN');
  check('progressIndex', progressIndex('newProfessor') === 0 && progressIndex('who') === 0 && progressIndex('comment') === 6 && progressIndex('thanks') === 7);

  // withMyRating
  const mine: Answers = { comment: '', rating: 2, hours: '10+', grade: 'A' };
  const fresh = withMyRating(undefined, mine);
  check('withMyRating undefined summary',
    fresh.numRatings === 1 && fresh.hoursCount === 1 && fresh.gradeCount === 1 &&
    fresh.rating === 2 && fresh.hoursPerWeek === 12 && same(fresh.gradeDistribution, { A: 1 }));
  const merged = withMyRating({ rating: 4, numRatings: 3, hoursPerWeek: 5, hoursCount: 1, gradeDistribution: { A: 1 }, gradeCount: 1 }, mine);
  check('withMyRating weighted merge',
    same(merged, { rating: 3.5, numRatings: 4, hoursPerWeek: 8.5, hoursCount: 2, gradeDistribution: { A: 2 }, gradeCount: 2 }));
  const noGrade = withMyRating(undefined, { comment: '', rating: 5, hours: '0-2', grade: null });
  check('withMyRating null grade adds no grade', noGrade.gradeCount === 0 && same(noGrade.gradeDistribution, {}));

  // buildSubmission
  const start: RateStart = { opened: 'tok', deviceToken: 'dev', counter: 3 };
  const sub = buildSubmission({ comment: 'ok', professor: newProf, course: newCourse, term: null, rating: 4, hours: '3-4', grade: null }, start, 'ts', 'hp');
  check('buildSubmission new professor and course',
    same(sub.professor, { name: 'Sam Newhire', department: null }) && same(sub.course, { code: 'CS9999', name: 'Brand New' }));
  check('buildSubmission passthrough',
    sub.term === null && sub.grade === null && sub.rating === 4 && sub.hours === '3-4' && sub.comment === 'ok' &&
    sub.opened === 'tok' && sub.deviceToken === 'dev' && sub.website === 'hp' && sub.turnstileToken === 'ts');
  const sub2 = buildSubmission({ ...a, comment: 'x' }, start, 't', '');
  check('buildSubmission existing professor and course', same(sub2.professor, { slug: 'alex-rivera' }) && same(sub2.course, { code: 'CS2500' }));

  console.log(fails.length ? `${fails.length} FAIL(s): ` + fails.join(', ') : 'ALL PASS');
  return fails.length ? 1 : 0;
}

if (argv[1] && fileURLToPath(import.meta.url) === argv[1]) {
  process.exit(selftest());
}
