import type { ReactNode } from 'react';
import type { RatePairSummary } from '../../api/api';
import { OVERALL, displayTerm, formatCode, hoursBucket, initials } from './rateFlow';
import type { Answers, StepId } from './rateFlow';

interface SidePanelProps {
  answers: Answers;
  step: StepId;
  counter: number | null;
  summary?: RatePairSummary;
  // Answers are being sent or already sent, so they can't be changed.
  locked?: boolean;
  onEdit: (step: StepId) => void;
}

const count = (n: number) => `${n} ${n === 1 ? 'rating' : 'ratings'}`;

function Pencil() {
  return (
    <svg className="rate-pen" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M12 20h9" />
      <path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z" />
    </svg>
  );
}

function Stat({ label, value }: { label: string; value: string | null }) {
  return (
    <div className="rate-stat">
      <span>{label}</span>
      {value === null ? <b className="rate-none">No data yet: you'd be first</b> : <b>{value}</b>}
    </div>
  );
}

function Row({ label, value, editStep, onEdit }: { label: string; value: ReactNode; editStep: StepId | null; onEdit: (s: StepId) => void }) {
  if (value === null || value === undefined || value === '') {
    return <div className="rate-stat"><span>{label}</span><b className="rate-todo">—</b></div>;
  }
  if (editStep === null) {
    return <div className="rate-stat"><span>{label}</span><b>{value}</b></div>;
  }
  return (
    <button type="button" className="rate-stat rate-edit" onClick={() => onEdit(editStep)}>
      <span>{label}</span>
      <b>{value}<Pencil /></b>
    </button>
  );
}

export function SidePanel({ answers, step, counter, summary, locked = false, onEdit }: SidePanelProps) {
  const { professor, course } = answers;

  if (!professor) {
    return (
      <aside className="rate-pane rate-side" aria-label="About rating">
        {counter !== null && (
          <section className="rate-box">
            <h2>This term</h2>
            <div className="rate-big-num">{counter}</div>
            <p>Huskies have rated a professor</p>
          </section>
        )}
        <section className="rate-box">
          <h2>How it works</h2>
          <div className="rate-stat"><span>Anonymous</span><b>No login</b></div>
          <div className="rate-stat"><span>Time</span><b>About 30 seconds</b></div>
          <div className="rate-stat"><span>Questions</span><b>5 quick taps</b></div>
        </section>
        <p className="rate-why">Every rating helps another student pick the right professor.</p>
      </aside>
    );
  }

  const thanks = step === 'thanks';
  const edit = (to: StepId) => (locked ? null : to);
  const overall = OVERALL.find((o) => o.value === answers.rating);
  const hours = answers.hours?.replace('-', '–');
  const grade = answers.grade === undefined ? null : answers.grade === null ? 'Not shared' : answers.grade;
  const term = answers.term === undefined ? null : answers.term === null ? 'Not sure' : displayTerm(answers.term);

  return (
    <aside className="rate-pane rate-side" aria-label="Your rating so far">
      <div className="rate-who">
        <div className="rate-av" aria-hidden="true">{initials(professor.name)}</div>
        <div>
          <h2>Prof. {professor.name}</h2>
          {professor.department && <p>{professor.department}</p>}
          {course && <span className="rate-pchip">{formatCode(course.code)}</span>}
          {answers.term !== undefined && <span className="rate-pchip">{term}</span>}
        </div>
      </div>
      {course && summary && (
        <section className="rate-box">
          <h2>{thanks ? `${formatCode(course.code)}, now with your rating` : `What students say about ${formatCode(course.code)}`}</h2>
          <Stat
            label="Overall"
            value={summary.rating !== null && summary.numRatings > 0 ? `${summary.rating.toFixed(1)} · ${count(summary.numRatings)}` : null}
          />
          <Stat
            label="Hours / week"
            value={summary.hoursPerWeek !== null && summary.hoursCount > 0 ? `${hoursBucket(summary.hoursPerWeek)} · ${count(summary.hoursCount)}` : null}
          />
          <Stat label="Grades" value={summary.gradeCount > 0 ? count(summary.gradeCount) : null} />
        </section>
      )}
      <section className="rate-box">
        <h2>Your rating</h2>
        <Row label="Course" value={course ? formatCode(course.code) : null} editStep={edit('course')} onEdit={onEdit} />
        <Row label="Term" value={term} editStep={edit('term')} onEdit={onEdit} />
        <Row label="Overall" value={overall ? `${overall.value} · ${overall.label}` : null} editStep={edit('overall')} onEdit={onEdit} />
        <Row label="Hours" value={hours ? `${hours} / wk` : null} editStep={edit('workload')} onEdit={onEdit} />
        <Row label="Grade" value={grade} editStep={edit('grade')} onEdit={onEdit} />
        <Row label="Comment" value={answers.comment.trim() ? 'Added' : null} editStep={edit('comment')} onEdit={onEdit} />
        {!locked && <p className="rate-note">Click an answer to change it</p>}
      </section>
    </aside>
  );
}
