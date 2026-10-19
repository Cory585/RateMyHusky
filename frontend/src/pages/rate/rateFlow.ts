// Step logic and pure helpers for the /rate form. No React, no runtime imports from api.ts.
import type { ProfessorCourse, RatePairSummary, RateStart, RateSubmission } from '../../api/api';

export type StepId = 'who' | 'newProfessor' | 'course' | 'otherCourse' | 'term' | 'otherTerm'
  | 'overall' | 'workload' | 'grade' | 'comment' | 'thanks';

export const QUESTION_COUNT = 7;

export interface RateProfessor { slug: string | null; name: string; department: string | null }
export interface RateCourse { code: string; name: string | null; isNew: boolean; terms: string[] }
export interface Answers {
  professor?: RateProfessor;
  course?: RateCourse;
  term?: string | null;
  rating?: number;
  hours?: string;
  grade?: string | null;
  comment: string;
}

export const OVERALL: { value: number; label: string }[] = [
  { value: 5, label: 'Amazing' },
  { value: 4, label: 'Good' },
  { value: 3, label: 'Okay' },
  { value: 2, label: 'Poor' },
  { value: 1, label: 'Awful' },
];

// value is what the server accepts (ASCII hyphen); label is what the student sees.
export const HOURS: { value: string; label: string; aria: string }[] = [
  { value: '0-2', label: '0–2 hrs', aria: '0 to 2 hours' },
  { value: '3-4', label: '3–4 hrs', aria: '3 to 4 hours' },
  { value: '5-7', label: '5–7 hrs', aria: '5 to 7 hours' },
  { value: '8-10', label: '8–10 hrs', aria: '8 to 10 hours' },
  { value: '10+', label: '10+ hrs', aria: 'More than 10 hours' },
];

const HOURS_MIDPOINT: Record<string, number> = { '0-2': 1, '3-4': 3.5, '5-7': 6, '8-10': 9, '10+': 12 };

export const GRADES: string[] = ['A', 'A-', 'B+', 'B', 'B-', 'C+', 'C', 'C- or below'];

export const SEASONS: { value: string; label: string }[] = [
  { value: 'Fall', label: 'Fall' },
  { value: 'Spring', label: 'Spring' },
  { value: 'Summer 1', label: 'Summer 1' },
  { value: 'Summer 2', label: 'Summer 2' },
  { value: 'Summer', label: 'Full Summer' },
];

const MAIN_ORDER = ['who', 'course', 'term', 'overall', 'workload', 'grade', 'comment'] as const;
type MainStep = typeof MAIN_ORDER[number];

function mainOf(step: StepId): MainStep | null {
  switch (step) {
    case 'newProfessor': return 'who';
    case 'otherCourse': return 'course';
    case 'otherTerm': return 'term';
    case 'thanks': return null;
    default: return step;
  }
}

export function progressIndex(step: StepId): number {
  const main = mainOf(step);
  return main === null ? QUESTION_COUNT : MAIN_ORDER.indexOf(main);
}

function isAnswered(step: MainStep, a: Answers): boolean {
  switch (step) {
    case 'who': return a.professor !== undefined;
    case 'course': return a.course !== undefined;
    case 'term': return a.term !== undefined;
    case 'overall': return a.rating !== undefined;
    case 'workload': return a.hours !== undefined;
    case 'grade': return a.grade !== undefined;
    case 'comment': return false;
  }
}

// The screen to show for a question, after the two redirects.
function screenFor(step: MainStep, a: Answers): StepId {
  if (step === 'course' && a.professor?.slug === null) return 'otherCourse';
  if (step === 'term' && !a.course?.terms.length) return 'otherTerm';
  return step;
}

// The screen an edit from the side panel opens: the picker, or its fallback for a new professor or course.
export function editScreen(step: 'course' | 'term', a: Answers): StepId {
  return screenFor(step, a);
}

function firstUnansweredFrom(index: number, a: Answers): StepId {
  for (let i = index; i < MAIN_ORDER.length; i++) {
    if (!isAnswered(MAIN_ORDER[i], a)) return screenFor(MAIN_ORDER[i], a);
  }
  return 'thanks';
}

export function firstStep(a: Answers): StepId {
  return firstUnansweredFrom(0, a);
}

export function nextStep(step: StepId, a: Answers): StepId {
  const main = mainOf(step);
  // Scans from the start so an edit can't skip a question the student hasn't answered.
  return main === null || main === 'comment' ? 'thanks' : firstUnansweredFrom(0, a);
}

export function prevStep(step: StepId, a: Answers): StepId | null {
  const main = mainOf(step);
  if (main === null || step === 'who') return null;
  if (step === 'newProfessor') return 'who';
  if (step === 'otherCourse') return a.professor?.slug === null ? 'newProfessor' : 'course';
  if (step === 'otherTerm' && a.course?.terms.length) return 'term';
  return screenFor(MAIN_ORDER[MAIN_ORDER.indexOf(main) - 1], a);
}

function sameProfessor(x: RateProfessor | undefined, y: RateProfessor): boolean {
  return x !== undefined && x.slug === y.slug && (x.slug !== null || x.name === y.name);
}

export function answer(a: Answers, change: Partial<Answers>): Answers {
  const next: Answers = { ...a, ...change };
  if (change.professor !== undefined && !sameProfessor(a.professor, change.professor)) {
    if (!('course' in change)) delete next.course;
    if (!('term' in change)) delete next.term;
  }
  if (change.course !== undefined && a.course?.code !== change.course.code) {
    if (!('term' in change)) delete next.term;
  }
  return next;
}

export function termYears(now: Date): number[] {
  const y = now.getFullYear();
  return Array.from({ length: 7 }, (_, i) => y - i);
}

export function formatCode(code: string): string {
  return code.replace(/^([A-Za-z]+)(\d)/, '$1 $2');
}

export function displayTerm(label: string): string {
  return label.replace(/^Summer (\d{4})$/, 'Full Summer $1');
}

export function lastName(name: string): string {
  return name.trim().split(/\s+/).pop() ?? '';
}

export function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return '';
  const first = parts[0][0];
  return (parts.length === 1 ? first : first + parts[parts.length - 1][0]).toUpperCase();
}

export function contextChip(a: Answers): string {
  if (!a.professor) return '';
  if (!a.course) return `Prof. ${a.professor.name}`;
  const parts = [lastName(a.professor.name), formatCode(a.course.code)];
  if (a.term !== undefined) parts.push(a.term === null ? 'Term not sure' : displayTerm(a.term));
  return parts.join(' · ');
}

export function hoursBucket(hours: number): string {
  if (hours < 2.5) return '0–2';
  if (hours < 4.5) return '3–4';
  if (hours < 7.5) return '5–7';
  if (hours < 10.5) return '8–10';
  return '10+';
}

export function prefillCourse(courses: ProfessorCourse[], code: string | null): RateCourse | undefined {
  if (!code) return undefined;
  const c = courses.find((x) => x.code === code);
  return c && { code: c.code, name: c.name, isNew: false, terms: c.terms };
}

const round2 = (n: number) => Math.round(n * 100) / 100;

export function withMyRating(s: RatePairSummary | undefined, a: Answers): RatePairSummary {
  const base: RatePairSummary = s ?? {
    rating: null, numRatings: 0, hoursPerWeek: null, hoursCount: 0, gradeDistribution: {}, gradeCount: 0,
  };
  const out: RatePairSummary = { ...base, gradeDistribution: { ...base.gradeDistribution } };
  if (a.rating !== undefined) {
    out.rating = base.rating === null ? a.rating : round2((base.rating * base.numRatings + a.rating) / (base.numRatings + 1));
    out.numRatings = base.numRatings + 1;
  }
  if (a.hours !== undefined) {
    const mine = HOURS_MIDPOINT[a.hours];
    out.hoursPerWeek = base.hoursPerWeek === null ? mine : round2((base.hoursPerWeek * base.hoursCount + mine) / (base.hoursCount + 1));
    out.hoursCount = base.hoursCount + 1;
  }
  if (a.grade) {
    out.gradeDistribution[a.grade] = (base.gradeDistribution[a.grade] ?? 0) + 1;
    out.gradeCount = base.gradeCount + 1;
  }
  return out;
}

export function buildSubmission(a: Answers, start: RateStart, turnstileToken: string, honeypot: string): RateSubmission {
  if (!a.professor || !a.course || a.rating === undefined || a.hours === undefined) {
    throw new Error('Incomplete rating');
  }
  return {
    professor: a.professor.slug !== null
      ? { slug: a.professor.slug }
      : { name: a.professor.name, department: a.professor.department },
    course: a.course.isNew ? { code: a.course.code, name: a.course.name ?? '' } : { code: a.course.code },
    term: a.term ?? null,
    rating: a.rating,
    hours: a.hours,
    grade: a.grade ?? null,
    comment: a.comment,
    opened: start.opened,
    deviceToken: start.deviceToken,
    website: honeypot,
    turnstileToken,
  };
}
