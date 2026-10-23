import { useLayoutEffect, useRef, useState } from 'react';
import { StepFoot } from './ChoiceGroup';

interface CommentStepProps {
  value: string;
  onChange: (v: string) => void;
  onSubmit: (skip: boolean, honeypot: string) => void;
  onBack: () => void;
  submitting: boolean;
  error: string;
}

export function CommentStep({ value, onChange, onSubmit, onBack, submitting, error }: CommentStepProps) {
  const [honeypot, setHoneypot] = useState('');
  const ref = useRef<HTMLTextAreaElement>(null);

  // Grows with the text; the CSS max-height caps it and the box scrolls inside past that.
  useLayoutEffect(() => {
    const t = ref.current;
    if (!t) return;
    t.style.height = 'auto';
    t.style.height = `${t.scrollHeight + t.offsetHeight - t.clientHeight}px`;
  }, [value]);

  return (
    <>
      <h1 className="rate-q" tabIndex={-1}>Anything other students should know?</h1>
      <p className="rate-sub">Optional. Tips, how exams felt, what to expect.</p>
      <textarea
        ref={ref}
        className="rate-input rate-textarea"
        aria-label="Anything other students should know?"
        maxLength={2000}
        rows={5}
        placeholder="Optional"
        value={value}
        readOnly={submitting}
        onChange={(e) => onChange(e.target.value)}
      />
      <input
        type="text"
        name="website"
        className="rate-hp"
        tabIndex={-1}
        autoComplete="off"
        aria-hidden="true"
        value={honeypot}
        onChange={(e) => setHoneypot(e.target.value)}
      />
      {error && <p className="rate-error" role="alert">{error}</p>}
      <StepFoot onBack={onBack} backDisabled={submitting}>
        <button type="button" className="rate-btn" disabled={submitting} onClick={() => onSubmit(false, honeypot)}>
          {submitting ? 'Checking…' : 'Submit rating'}
        </button>
        <button type="button" className="rate-ghost" disabled={submitting} onClick={() => onSubmit(true, honeypot)}>
          Skip and submit
        </button>
      </StepFoot>
    </>
  );
}
