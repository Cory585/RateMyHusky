import { useEffect, useState } from 'react';
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

export function Pencil() {
  return (
    <span className="rate-pencil" aria-hidden="true">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M12 20h9" />
        <path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z" />
      </svg>
    </span>
  );
}

export function CourseStep({ professor, courses, ratedCourses, selected, onPick, onOther }: CourseStepProps) {
  const option = (c: RateCourse): ChoiceOption<string> => {
    const rated = ratedCourses.includes(c.code);
    return {
      value: c.code,
      aria: `${formatCode(c.code)}${c.name ? `, ${c.name}` : ''}${rated ? ', update your rating' : ''}`,
      className: rated ? 'course rated' : 'course',
      label: (
        <>
          <span className="rate-cmain"><b>{formatCode(c.code)}</b>{c.name && <span className="rate-cname">{c.name}</span>}</span>
          {rated && <Pencil />}
        </>
      ),
    };
  };
  const options = courses.filter((c) => !ratedCourses.includes(c.code)).map(option);
  options.push({ value: OTHER, aria: 'A different course', label: '+ A different course', className: 'course dash' });
  const rated = courses.filter((c) => ratedCourses.includes(c.code)).map(option);
  if (rated.length) rated[0].heading = 'Already rated';
  options.push(...rated);

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
          <p className="rate-hint rate-note">{formatCode(code)} isn't in our catalog yet. Name it and it's added with your rating.</p>
          <div className="rate-field">
            <label htmlFor="rate-course-name">Course name</label>
            <input
              id="rate-course-name"
              className="rate-input"
              autoComplete="off"
              placeholder="e.g. Topics: Applied ML"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
            />
          </div>
        </>
      )}
      {settled && settled.length === 0 && !NEW_CODE.test(code) && (
        <p className="rate-hint rate-note" role="status">No courses match. Type the full code, like CS 4999, to add a new one.</p>
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
