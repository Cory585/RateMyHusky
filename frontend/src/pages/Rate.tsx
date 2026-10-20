import { useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { fetchProfessorFull, fetchRateContext, startRating, submitRating } from '../api/api';
import type { ProfessorPage, RatePairSummary, RateProfessorContext, RateStart, RateSubmission } from '../api/api';
import { useTurnstile } from './rate/useTurnstile';
import { WhoStep, NewProfessorStep } from './rate/WhoStep';
import { CourseStep, OtherCourseStep } from './rate/CourseStep';
import { TermStep, OtherTermStep } from './rate/TermStep';
import { CommentStep } from './rate/CommentStep';
import { ThanksStep } from './rate/ThanksStep';
import { Celebration } from './rate/Celebration';
import { useMediaQuery } from './rate/useMediaQuery';
import { ChoiceGroup, StepFoot } from './rate/ChoiceGroup';
import { SidePanel } from './rate/SidePanel';
import type { ChoiceOption } from './rate/ChoiceGroup';
import {
  GRADES, HOURS, OVERALL, QUESTION_COUNT, answer, buildSubmission, contextChip, editScreen, firstStep, lastName, nextStep,
  prefillCourse, prevStep, progressIndex, withMyRating,
} from './rate/rateFlow';
import type { Answers, RateCourse, RateProfessor, StepId } from './rate/rateFlow';
import './Rate.css';

const DEVICE_KEY = 'rmh_device';

function readMirror(): string | null {
  try {
    return localStorage.getItem(DEVICE_KEY);
  } catch {
    return null;
  }
}

function writeMirror(token: string) {
  try {
    localStorage.setItem(DEVICE_KEY, token);
  } catch {
    /* the cookie still identifies the device */
  }
}

const FAILURE_MESSAGES: Record<number, string> = {
  403: "We couldn't check you're human. Try again.",
  422: "This comment can't be posted. Edit it or skip it.",
  429: 'Too many ratings from this device for now. Try again later.',
};
const TOKEN_WAIT_MS = 15000;
const NETWORK_MESSAGE = "Couldn't send. Check your connection and try again.";

// What a pair with no ratings from any source looks like.
const NO_DATA: RatePairSummary = {
  rating: null, numRatings: 0, hoursPerWeek: null, hoursCount: 0, gradeDistribution: {}, gradeCount: 0,
};

const gradeAria = (g: string) => `Grade ${g.replace('+', ' plus').replace('-', ' minus')}`;

const toRateCourses = (page: ProfessorPage): RateCourse[] =>
  page.courses.map((c) => ({ code: c.code, name: c.name, isNew: false, terms: c.terms }));

export default function Rate() {
  const [params] = useSearchParams();
  const [ready, setReady] = useState(false);
  const [answers, setAnswers] = useState<Answers>({ comment: '' });
  const [step, setStep] = useState<StepId>('who');
  const [start, setStart] = useState<RateStart | null>(null);
  const [startFailed, setStartFailed] = useState(false);
  const [loaded, setLoaded] = useState<{ slug: string; list: RateCourse[] } | null>(null);
  const [context, setContext] = useState<RateProfessorContext | null>(null);
  const [typedName, setTypedName] = useState('');
  const [counter, setCounter] = useState<number | null>(null);
  const [submitError, setSubmitError] = useState('');
  const [pending, setPending] = useState<{ skip: boolean; honeypot: string } | null>(null);
  const [sending, setSending] = useState(false);
  const [contextReload, setContextReload] = useState(0);
  // The pair's numbers as they were before this rating, which the thanks screen adds it to.
  const [thanksBase, setThanksBase] = useState<RatePairSummary | null>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const { containerRef, token, failed, reset } = useTurnstile();
  const isDesktop = useMediaQuery('(min-width: 1024px)');

  const slug = answers.professor?.slug ?? null;
  // null while the professor's courses load; a new professor has none to load.
  const courses = slug === null ? [] : loaded?.slug === slug ? loaded.list : null;
  const coursesLoading = courses === null;

  // Start the visit, then resolve any ?professor / ?course prefill.
  useEffect(() => {
    let cancelled = false;
    startRating(readMirror())
      .then((s) => {
        if (cancelled) return;
        writeMirror(s.deviceToken);
        setStart(s);
      })
      .catch(() => { if (!cancelled) setStartFailed(true); });

    (async () => {
      const profSlug = params.get('professor');
      let initial: Answers = { comment: '' };
      if (profSlug) {
        const res = await fetchProfessorFull(profSlug);
        if (res.ok) {
          const { identity } = res.data;
          const professor: RateProfessor = { slug: identity.slug, name: identity.name, department: identity.department };
          const course = prefillCourse(res.data.courses, (params.get('course') ?? '').replace(/\s+/g, '').toUpperCase() || null);
          initial = answer(initial, { professor, ...(course && { course }) });
        }
      }
      if (cancelled) return;
      setAnswers(initial);
      setStep(firstStep(initial));
      setReady(true);
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- the prefill is read once, on mount
  }, []);

  useEffect(() => {
    if (!slug) return;
    let cancelled = false;
    fetchProfessorFull(slug)
      .then((res) => { if (!cancelled) setLoaded({ slug, list: res.ok ? toRateCourses(res.data) : [] }); })
      .catch(() => { if (!cancelled) setLoaded({ slug, list: [] }); });
    return () => { cancelled = true; };
  }, [slug]);

  // The context call reads the cookie that startRating sets, so it waits for start.
  useEffect(() => {
    if (!start || !slug) return;
    let cancelled = false;
    fetchRateContext(slug)
      .then((c) => { if (!cancelled) setContext(c); })
      .catch(() => { /* no "You rated this" tags */ });
    return () => { cancelled = true; };
  }, [start, slug, contextReload]);

  // Undefined until the numbers are known, so the panel never claims "no data" while loading or after a failed load.
  const knownPair: RatePairSummary | undefined = !answers.course ? undefined
    : answers.professor?.slug === null ? NO_DATA
    : context ? context.summaries[answers.course.code] ?? NO_DATA : undefined;

  const send = (skip: boolean, honeypot: string, turnstileToken: string) => {
    if (!start) return;
    let body: RateSubmission;
    try {
      body = buildSubmission({ ...answers, comment: skip ? '' : answers.comment }, start, turnstileToken, honeypot);
    } catch {
      setStep(firstStep(answers));
      return;
    }
    setSending(true);
    submitRating(body).then((res) => {
      setSending(false);
      if (res.ok) {
        if (skip) setAnswers((a) => ({ ...a, comment: '' }));
        setCounter(res.counter);
        setThanksBase(knownPair ?? null);
        reset();
        setStep('thanks');
        setContextReload((n) => n + 1);
        return;
      }
      // Turnstile tokens are single-use, so every retry needs a fresh one.
      reset();
      setSubmitError(FAILURE_MESSAGES[res.status] ?? (res.error || NETWORK_MESSAGE));
    });
  };

  const submit = (skip: boolean, honeypot: string) => {
    setSubmitError('');
    if (token && start) send(skip, honeypot, token);
    else {
      setPending({ skip, honeypot });
      if (!start) {
        setStartFailed(false);
        startRating(readMirror())
          .then((s) => {
            writeMirror(s.deviceToken);
            setStart(s);
          })
          .catch(() => setStartFailed(true));
      }
    }
  };

  // A submit that arrived before the Turnstile token or the visit start waits here.
  useEffect(() => {
    if (!pending || !token || !start) return;
    setPending(null);
    send(pending.skip, pending.honeypot, token);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- fires once when the token arrives
  }, [pending, token, start]);

  // Bound the wait for the token or the visit start so a blocked challenge can't leave the step stuck.
  useEffect(() => {
    if (!pending) return;
    const giveUp = () => {
      setPending(null);
      setSubmitError(start ? FAILURE_MESSAGES[403] : NETWORK_MESSAGE);
      reset();
    };
    if (failed) {
      giveUp();
      return;
    }
    const timer = setTimeout(giveUp, TOKEN_WAIT_MS);
    return () => clearTimeout(timer);
  }, [pending, failed, reset, start]);

  const rateAnother = () => {
    setAnswers({ comment: '' });
    setStep('who');
    setStart(null);
    setStartFailed(false);
    setContext(null);
    setCounter(null);
    setSubmitError('');
    setTypedName('');
    setContextReload((n) => n + 1);
    startRating(readMirror())
      .then((s) => {
        writeMirror(s.deviceToken);
        setStart(s);
      })
      .catch(() => setStartFailed(true));
  };

  useEffect(() => {
    if (ready) panelRef.current?.querySelector<HTMLElement>('h1')?.focus({ preventScroll: true });
  }, [step, ready, coursesLoading]);

  const go = (next: Answers, from: StepId) => {
    setAnswers(next);
    setStep(nextStep(from, next));
  };

  const pickProfessor = (p: RateProfessor, from: StepId) => {
    if (p.slug !== slug) setContext(null);
    go(answer(answers, { professor: p }), from);
  };

  const back = () => {
    const prev = prevStep(step, answers);
    if (prev) setStep(prev);
  };

  const renderStep = () => {
    switch (step) {
      case 'who':
        return (
          <>
            <WhoStep
              counter={start?.counter ?? null}
              onPick={(p) => pickProfessor(p, 'who')}
              onAddNew={(name) => { setTypedName(name); setStep('newProfessor'); }}
            />
            <StepFoot />
          </>
        );
      case 'newProfessor':
        return <NewProfessorStep initialName={typedName} onContinue={(p) => pickProfessor(p, 'newProfessor')} onBack={back} />;
      case 'course':
        return answers.professor && courses === null ? (
          <>
            <h1 className="rate-q" tabIndex={-1}>Which course?</h1>
            <p className="rate-hint" role="status">Loading courses…</p>
            <StepFoot onBack={back} />
          </>
        ) : answers.professor && courses && (
          <>
            <CourseStep
              professor={answers.professor}
              courses={courses}
              ratedCourses={context?.ratedCourses ?? []}
              selected={answers.course?.code}
              onPick={(c) => go(answer(answers, { course: c }), 'course')}
              onOther={() => setStep('otherCourse')}
            />
            <StepFoot onBack={back} />
          </>
        );
      case 'otherCourse':
        return <OtherCourseStep onPick={(c) => go(answer(answers, { course: c }), 'otherCourse')} onBack={back} />;
      case 'term':
        return answers.course && (
          <>
            <TermStep
              professor={answers.professor!}
              course={answers.course}
              selected={answers.term ?? undefined}
              onPick={(t) => go(answer(answers, { term: t }), 'term')}
              onOther={() => setStep('otherTerm')}
            />
            <StepFoot onBack={back} />
          </>
        );
      case 'otherTerm':
        return <OtherTermStep now={new Date()} onPick={(t) => go(answer(answers, { term: t }), 'otherTerm')} onBack={back} />;
      case 'overall': {
        const options: ChoiceOption<number>[] = OVERALL.map((o) => ({
          value: o.value,
          aria: `${o.value} out of 5, ${o.label}`,
          digit: o.value,
          label: (
            <>
              <span className="rate-n" data-score={o.value}>{o.value}</span>
              <span className="rate-n-label">{o.label}</span>
            </>
          ),
        }));
        return (
          <>
            <ChoiceGroup
              legend={`How was Prof. ${lastName(answers.professor?.name ?? '')} overall?`}
              subtitle="Would you recommend them to a friend?"
              options={isDesktop ? [...options].reverse() : options}
              value={answers.rating}
              layout={isDesktop ? 'row' : 'stack'}
              onPick={(v) => go(answer(answers, { rating: v }), 'overall')}
            />
            <StepFoot onBack={back} />
          </>
        );
      }
      case 'workload':
        return (
          <>
            <ChoiceGroup
              legend="Hours per week outside class?"
              subtitle="Homework, studying, projects. Your best guess is fine."
              options={HOURS.map((h, i) => ({ value: h.value, label: h.label, aria: h.aria, digit: i + 1 }))}
              value={answers.hours}
              layout={isDesktop ? 'row' : 'stack'}
              onPick={(v) => go(answer(answers, { hours: v }), 'workload')}
            />
            <StepFoot onBack={back} />
          </>
        );
      case 'grade':
        return (
          <>
            <ChoiceGroup
              legend="What grade did you get?"
              subtitle="Helps students see how this course is graded."
              options={GRADES.map((g) => ({ value: g, label: g, aria: gradeAria(g) }))}
              value={answers.grade ?? undefined}
              layout="grid"
              onPick={(v) => go(answer(answers, { grade: v }), 'grade')}
            />
            <button type="button" className="rate-ghost" onClick={() => go(answer(answers, { grade: null }), 'grade')}>
              Prefer not to say
            </button>
            <StepFoot onBack={back} />
          </>
        );
      case 'comment':
        return (
          <CommentStep
            value={answers.comment}
            onChange={(comment) => setAnswers((a) => ({ ...a, comment }))}
            onSubmit={submit}
            onBack={back}
            submitting={sending || pending !== null}
            error={submitError}
          />
        );
      case 'thanks':
        return <ThanksStep answers={answers} counter={counter} onRateAnother={rateAnother} popperSlot={<Celebration />} />;
      default:
        return null;
    }
  };

  const chip = contextChip(answers);
  const edit = (to: StepId) => setStep(to === 'course' || to === 'term' ? editScreen(to, answers) : to);

  return (
    <div className="rate-page">
      <div className="rate-shell">
        {isDesktop && ready && (
          <SidePanel
            answers={answers}
            step={step}
            counter={start?.counter ?? null}
            summary={step === 'thanks' ? (thanksBase ? withMyRating(thanksBase, answers) : undefined) : knownPair}
            locked={step === 'thanks' || sending || pending !== null}
            onEdit={edit}
          />
        )}
        <div className="rate-panel" ref={panelRef}>
          {step !== 'thanks' && (
            <div className="rate-prog" aria-hidden="true">
              {Array.from({ length: QUESTION_COUNT }, (_, i) => (
                <i key={i} className={i <= progressIndex(step) ? 'on' : undefined} />
              ))}
            </div>
          )}
          {startFailed && <p className="rate-hint" role="alert">We couldn't start the form. Please reload and try again.</p>}
          {ready && chip && step !== 'thanks' && <div className="rate-chip">{chip}</div>}
          {ready && renderStep()}
          <div ref={containerRef} className="rate-turnstile" />
        </div>
      </div>
    </div>
  );
}
