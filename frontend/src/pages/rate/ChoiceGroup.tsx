import { useEffect, useId, useRef } from 'react';
import type { CSSProperties, KeyboardEvent, ReactNode } from 'react';

export interface ChoiceOption<T> {
  value: T;
  label: ReactNode;
  aria: string;
  className?: string;
  digit?: number;
}

interface ChoiceGroupProps<T> {
  legend: string;
  subtitle?: string;
  options: ChoiceOption<T>[];
  value?: T;
  onPick: (v: T) => void;
  layout: 'stack' | 'row' | 'grid';
}

export function BackLink({ onBack, disabled }: { onBack: () => void; disabled?: boolean }) {
  return <button type="button" className="rate-back" disabled={disabled} onClick={onBack}>‹ Back</button>;
}

/* On mobile this renders in place; from 1024px it is the pinned footer, Back left and the step's action right. */
export function StepFoot({ onBack, backDisabled, children }: { onBack?: () => void; backDisabled?: boolean; children?: ReactNode }) {
  return (
    <div className="rate-foot">
      <span className="rate-foot-actions">{children}</span>
      {onBack && <BackLink onBack={onBack} disabled={backDisabled} />}
    </div>
  );
}

export function ChoiceGroup<T>({ legend, subtitle, options, value, onPick, layout }: ChoiceGroupProps<T>) {
  const headingId = useId();
  const groupRef = useRef<HTMLDivElement>(null);

  // Keys 1-5 pick the option whose digit matches, never by position.
  useEffect(() => {
    const onKey = (e: globalThis.KeyboardEvent) => {
      if (e.repeat || e.ctrlKey || e.metaKey || e.altKey) return;
      const el = document.activeElement;
      if (el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement || el instanceof HTMLSelectElement) return;
      if (!/^[1-5]$/.test(e.key)) return;
      const hit = options.find((o) => o.digit === Number(e.key));
      if (hit) onPick(hit.value);
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [options, onPick]);

  // Arrow keys move focus only; Enter, Space and click select.
  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const step = e.key === 'ArrowDown' || e.key === 'ArrowRight' ? 1 : e.key === 'ArrowUp' || e.key === 'ArrowLeft' ? -1 : 0;
    if (!step) return;
    const radios = Array.from(groupRef.current?.querySelectorAll<HTMLButtonElement>('[role="radio"]') ?? []);
    const at = radios.indexOf(document.activeElement as HTMLButtonElement);
    if (at < 0) return;
    e.preventDefault();
    radios[(at + step + radios.length) % radios.length].focus();
  };

  const checked = options.findIndex((o) => o.value === value);
  const tabStop = checked < 0 ? 0 : checked;

  return (
    <>
      <h1 id={headingId} className="rate-q" tabIndex={-1}>{legend}</h1>
      {subtitle && <p className="rate-sub">{subtitle}</p>}
      <div
        ref={groupRef}
        role="radiogroup"
        aria-labelledby={headingId}
        className={`rate-choices rate-choices-${layout}`}
        style={{ '--cols': options.length } as CSSProperties}
        onKeyDown={onKeyDown}
      >
        {options.map((o, i) => (
          <button
            key={i}
            type="button"
            role="radio"
            aria-checked={i === checked}
            aria-label={o.aria}
            tabIndex={i === tabStop ? 0 : -1}
            className={`rate-opt${i === checked ? ' on' : ''}${o.className ? ` ${o.className}` : ''}`}
            onClick={() => onPick(o.value)}
          >
            {o.label}
          </button>
        ))}
      </div>
    </>
  );
}
