import { useState } from 'react';
import { ChoiceGroup, StepFoot } from './ChoiceGroup';
import type { ChoiceOption } from './ChoiceGroup';
import { SEASONS, displayTerm, formatCode, lastName, termYears } from './rateFlow';
import type { RateCourse, RateProfessor } from './rateFlow';

const OTHER = '+other';

interface TermStepProps {
  professor: RateProfessor;
  course: RateCourse;
  selected?: string;
  onPick: (term: string | null) => void;
  onOther: () => void;
}

export function TermStep({ professor, course, selected, onPick, onOther }: TermStepProps) {
  const options: ChoiceOption<string>[] = course.terms.map((t) => ({ value: t, label: displayTerm(t), aria: displayTerm(t) }));
  options.push({ value: OTHER, aria: 'A different term', label: '+ A different term', className: 'dash' });
  return (
    <ChoiceGroup
      legend="When did you take it?"
      subtitle={`Terms Prof. ${lastName(professor.name)} taught ${formatCode(course.code)}, or pick any other.`}
      options={options}
      value={selected}
      layout="stack"
      onPick={(v) => (v === OTHER ? onOther() : onPick(v))}
    />
  );
}

interface OtherTermStepProps {
  now: Date;
  onPick: (term: string | null) => void;
  onBack: () => void;
}

export function OtherTermStep({ now, onPick, onBack }: OtherTermStepProps) {
  const years = termYears(now);
  const [season, setSeason] = useState<string>();
  const [year, setYear] = useState(years[0]);

  return (
    <>
      <h1 className="rate-q" tabIndex={-1}>When did you take it?</h1>
      <p className="rate-sub">Pick the term and year.</p>
      <div className="rate-field">
        <span id="rate-season-label" className="rate-label">Term</span>
        <div role="radiogroup" aria-labelledby="rate-season-label" className="rate-choices rate-choices-row">
          {SEASONS.map((s) => (
            <button
              key={s.value}
              type="button"
              role="radio"
              aria-checked={s.value === season}
              className={`rate-opt pill${s.value === season ? ' on' : ''}`}
              onClick={() => setSeason(s.value)}
            >
              {s.label}
            </button>
          ))}
        </div>
      </div>
      <div className="rate-field">
        <label htmlFor="rate-year">Year</label>
        <select id="rate-year" className="rate-input" value={year} onChange={(e) => setYear(Number(e.target.value))}>
          {years.map((y) => <option key={y} value={y}>{y}</option>)}
        </select>
      </div>
      <StepFoot onBack={onBack}>
        <button type="button" className="rate-btn" disabled={!season} onClick={() => onPick(`${season} ${year}`)}>
          Continue
        </button>
        <button type="button" className="rate-ghost" onClick={() => onPick(null)}>Not sure</button>
      </StepFoot>
    </>
  );
}
