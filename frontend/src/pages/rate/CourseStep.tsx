import { useEffect, useRef, useState } from 'react';
import { fetchSearchSuggestions } from '../../api/api';
import { useDebouncedValue } from '../../utils/useDebouncedValue';
import { ChoiceGroup, StepFoot } from './ChoiceGroup';
import type { ChoiceOption } from './ChoiceGroup';
import { formatCode, lastName } from './rateFlow';
import type { RateCourse, RateProfessor } from './rateFlow';

const OTHER = '+other';

interface CourseStepProps {
  professor: RateProfessor;
  courses: RateCourse[];
  ratedCourses: string[];
  selected?: string;
  onPick: (c: RateCourse) => void;
  onOther: () => void;
}

export function CourseStep({ professor, courses, ratedCourses, selected, onPick, onOther }: CourseStepProps) {
  const options: ChoiceOption<string>[] = courses.map((c) => {
    const rated = ratedCourses.includes(c.code);
    return {
      value: c.code,
      aria: `${formatCode(c.code)}${c.name ? `, ${c.name}` : ''}${rated ? ', you rated this' : ''}`,
      label: (
        <>
          <span>{formatCode(c.code)}{c.name ? ` · ${c.name}` : ''}</span>
          {rated && <span className="rate-tag">You rated this</span>}
        </>
      ),
    };
  });
  options.push({ value: OTHER, aria: 'A different course', label: '+ A different course', className: 'dash' });

  return (
    <ChoiceGroup
      legend="Which course?"
      subtitle={`Courses we know Prof. ${lastName(professor.name)} has taught.`}
      options={options}
      value={selected}
      layout="stack"
      onPick={(v) => (v === OTHER ? onOther() : onPick(courses.find((c) => c.code === v)!))}
    />
  );
}

const NEW_CODE = /^[A-Z]{2,5}\d{4}[A-Z]?$/;
// The catalog search only matches a code written without a space ("cs1800", not "CS 1800").
const CODE_LIKE = /^[A-Za-z]{2,5}\s*\d{1,4}[A-Za-z]?$/;

interface OtherCourseStepProps {
  onPick: (c: RateCourse) => void;
  onBack: () => void;
}

export function OtherCourseStep({ onPick, onBack }: OtherCourseStepProps) {
  const [query, setQuery] = useState('');
  const [newName, setNewName] = useState('');
  const nameRef = useRef<HTMLInputElement>(null);
  const typed = query.trim();
  const term = CODE_LIKE.test(typed) ? typed.replace(/\s+/g, '') : typed;
  const debounced = useDebouncedValue(term, 200);
  const [found, setFound] = useState<{ q: string; items: RateCourse[] } | null>(null);

  useEffect(() => {
    if (debounced.length < 2) return;
    let cancelled = false;
    fetchSearchSuggestions(debounced, 'Course')
      .then((rows) => {
        if (cancelled) return;
        const items = rows.flatMap((r) => (r.type === 'course' ? [{ code: r.code, name: r.name, isNew: false, terms: [] }] : []));
        setFound({ q: debounced, items: items.slice(0, 5) });
      })
      .catch(() => { /* leave the previous results */ });
    return () => { cancelled = true; };
  }, [debounced]);

  const settled = found !== null && found.q === term ? found.items : null;
  const code = typed.replace(/\s+/g, '').toUpperCase();
  const canAdd = settled !== null && settled.length === 0 && NEW_CODE.test(code);

  return (
    <>
      <h1 className="rate-q" tabIndex={-1}>Which course?</h1>
      <p className="rate-sub">Search every course, or add one we don't have.</p>
      <div className="rate-field">
        <label htmlFor="rate-course-search">Search all courses</label>
        <input
          id="rate-course-search"
          className="rate-input"
          autoComplete="off"
          placeholder="e.g. CS 2500"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      </div>
      {settled && settled.length > 0 && (
        <div className="rate-suggs">
          {settled.map((c) => (
            <button key={c.code} type="button" className="rate-sugg" onClick={() => onPick(c)}>
              <b>{formatCode(c.code)}</b>
              {c.name && <small>{c.name}</small>}
            </button>
          ))}
        </div>
      )}
      {canAdd && (
        <>
          <div className="rate-suggs">
            <button type="button" className="rate-sugg add" onClick={() => nameRef.current?.focus()}>
              + Not listed? Add "{formatCode(code)}" as a new course
            </button>
          </div>
          <div className="rate-field">
            <label htmlFor="rate-course-name">Course name (for new courses)</label>
            <input
              id="rate-course-name"
              ref={nameRef}
              className="rate-input"
              autoComplete="off"
              placeholder="e.g. Topics: Applied ML"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
            />
          </div>
        </>
      )}
      <StepFoot onBack={onBack}>
        {canAdd && (
          <button
            type="button"
            className="rate-btn"
            disabled={!newName.trim()}
            onClick={() => onPick({ code, name: newName.trim(), isNew: true, terms: [] })}
          >
            Continue
          </button>
        )}
      </StepFoot>
    </>
  );
}
