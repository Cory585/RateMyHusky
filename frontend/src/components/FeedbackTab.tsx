import { useState, useEffect, useRef, useCallback } from 'react';
import logo from '../assets/logo.jpg';
import Dropdown from './Dropdown';
import { submitFeedback } from '../api/api';
import { useAuth } from '../context/AuthContext';
import { ASK_ENABLED } from '../config';
import './FeedbackTab.css';

declare global {
  interface Window {
    turnstile?: {
      render: (container: string | HTMLElement, options: Record<string, unknown>) => string;
      remove: (widgetId: string) => void;
      reset: (widgetId: string) => void;
    };
  }
}

const TURNSTILE_SITE_KEY = import.meta.env.VITE_TURNSTILE_SITE_KEY;
if (!TURNSTILE_SITE_KEY && import.meta.env.PROD) {
  throw new Error('VITE_TURNSTILE_SITE_KEY environment variable is required in production');
}

// "incorrectdata", "general" and "mydata" also stand for the merged "missing", "feature",
// "professor" and "datadeletion", which server.py still accepts from cached bundles.
// "mydata" covers a professor's page and a student's account data, so it sends the
// account when signed in but never requires it.
const feedbackOptions = [
  { value: 'bug', label: 'Bug Report' },
  { value: 'incorrectdata', label: 'Wrong or Missing Data' },
  { value: 'general', label: 'Idea or General Feedback' },
  { value: 'mydata', label: 'Correct or Remove My Data' },
  { value: 'banappeal', label: 'Ask Ban Appeal' },
].filter((o) => ASK_ENABLED || o.value !== 'banappeal');

// Mirror server.py's FEEDBACK_MAX_DESCRIPTION / FEEDBACK_MAX_EMAIL and its email pattern;
// public/maintenance.html copies them too (maxlength attributes and its submit handler).
const MAX_DESCRIPTION = 5000;
const MAX_EMAIL = 254;
const EMAIL_PATTERN = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

// types where an email is required and (when signed in) the verified account id is sent,
// so we can respond and act on the right account; a professor is confirmed at their
// Northeastern address, which the FAQ, Privacy Policy and Terms say we need
const ACCOUNT_TYPES = ['banappeal', 'mydata'];

const FeedbackTab = () => {
  const { user, loading: authLoading } = useAuth();
  const [isOpen, setIsOpen] = useState(false);
  const [feedbackType, setFeedbackType] = useState('');
  const [description, setDescription] = useState('');
  const [email, setEmail] = useState('');
  const [submitted, setSubmitted] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [turnstileToken, setTurnstileToken] = useState('');
  const turnstileRef = useRef<HTMLDivElement>(null);
  const widgetIdRef = useRef<string | null>(null);

  useEffect(() => {
    const handler = () => setIsOpen(true);
    window.addEventListener('open-feedback', handler);
    return () => window.removeEventListener('open-feedback', handler);
  }, []);

  const renderTurnstile = useCallback(() => {
    if (!turnstileRef.current || !window.turnstile || !TURNSTILE_SITE_KEY) return;
    if (widgetIdRef.current !== null) {
      window.turnstile.remove(widgetIdRef.current);
      widgetIdRef.current = null;
    }
    widgetIdRef.current = window.turnstile.render(turnstileRef.current, {
      sitekey: TURNSTILE_SITE_KEY,
      callback: (token: string) => setTurnstileToken(token),
      'expired-callback': () => setTurnstileToken(''),
      theme: document.documentElement.classList.contains('dark') ? 'dark' : 'light',
      size: 'normal',
    });
  }, []);

  useEffect(() => {
    if (!isOpen || submitted) return;
    // Wait for Turnstile script to load
    const interval = setInterval(() => {
      if (window.turnstile) {
        clearInterval(interval);
        renderTurnstile();
      }
    }, 100);
    return () => {
      clearInterval(interval);
      if (widgetIdRef.current !== null && window.turnstile) {
        window.turnstile.remove(widgetIdRef.current);
        widgetIdRef.current = null;
      }
      setTurnstileToken('');
    };
  }, [isOpen, submitted, renderTurnstile]);

  const handleSubmit = async () => {
    if (!feedbackType || !description.trim()) {
      setError('Please select a feedback type and enter a description.');
      return;
    }
    if (ACCOUNT_TYPES.includes(feedbackType) && !email.trim()) {
      setError('Email is required for this request so we can respond.');
      return;
    }
    if (email.trim() && !EMAIL_PATTERN.test(email.trim())) {
      setError('Please enter a valid email address.');
      return;
    }
    if (!turnstileToken) {
      setError('Please complete the CAPTCHA verification.');
      return;
    }
    setError('');
    setLoading(true);
    try {
      // For ban-appeal / my-data, include the signed-in account id so we can locate
      // the ask_log rows; null when not logged in (the email body notes that case).
      const accountSub = ACCOUNT_TYPES.includes(feedbackType)
        ? (localStorage.getItem('auth_token') ?? undefined)
        : undefined;
      await submitFeedback({ feedbackType, description, email: email || undefined, turnstileToken, accountSub });
      setSubmitted(true);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : '';
      if (msg.includes('429') || msg.includes('limit')) {
        setError('Daily feedback limit reached. Please try again tomorrow.');
      } else if (msg.includes('Invalid email')) {
        setError('Please enter a valid email address.');
      } else if (msg.includes('too long')) {
        setError(`Please keep the description under ${MAX_DESCRIPTION.toLocaleString()} characters.`);
      } else if (msg.includes('CAPTCHA') || msg.includes('captcha')) {
        setError('CAPTCHA verification failed. Please try again.');
        if (widgetIdRef.current !== null && window.turnstile) {
          window.turnstile.reset(widgetIdRef.current);
        }
        setTurnstileToken('');
      } else {
        setError('Failed to send feedback. Please try again.');
      }
    } finally {
      setLoading(false);
    }
  };

  const handleClose = () => {
    setIsOpen(false);
    setTimeout(() => {
      setSubmitted(false);
      setFeedbackType('');
      setDescription('');
      setEmail('');
      setError('');
      setLoading(false);
      setTurnstileToken('');
    }, 300);
  };

  const getPlaceholder = () => {
    switch (feedbackType) {
      case 'bug':
        return "Describe the bug. What happened, and what did you expect to happen?";
      case 'incorrectdata':
        return "What's wrong or missing? Include the professor or course, a link to the page, and what it should say.";
      case 'banappeal':
        return "Explain why your access to Ask should be restored. Include any context about the questions you asked.";
      case 'mydata':
        return "Professors: which page, and what to fix or remove? We confirm at your Northeastern email. Students: we can delete your Ask logs and bookmarks.";
      case 'general':
        return "Share an idea for a feature, a suggestion, or anything else on your mind.";
      default:
        return "Describe your feedback here.";
    }
  };

  return (
    <>
      <button className="feedback-tab" onClick={() => setIsOpen(true)}>
        Feedback
      </button>
      <button className="feedback-fab" onClick={() => setIsOpen(true)} aria-label="Feedback">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
        </svg>
      </button>

      {isOpen && (
        <div className="feedback-overlay" onClick={handleClose}>
          <div
            className={`feedback-modal ${submitted ? 'feedback-modal-shrink' : ''}`}
            onClick={(e) => e.stopPropagation()}
          >
            <button className="feedback-close" onClick={handleClose}>
              ×
            </button>

            {submitted ? (
              <div className="feedback-success">
                <div className="feedback-success-icon">✓</div>
                <h2 className="feedback-title">Thank You!</h2>
                <p className="feedback-success-msg">
                  We've received your feedback and appreciate you taking the time to help us improve RateMyHusky.
                </p>
                <button className="feedback-done-btn" onClick={handleClose}>
                  Done
                </button>
              </div>
            ) : (
              <>
                <div className="feedback-mascot">
                  <img src={logo} alt="RateMyHusky Mascot" className="feedback-mascot-img" />
                </div>

                <h2 className="feedback-title">Feedback Form</h2>
                <p className="feedback-subtitle">
                  Spotted something off? Got ideas? We'd love to hear from you.
                </p>

                {error && <p className="feedback-error">{error}</p>}

                <label className="feedback-label">
                  Type of Feedback <span className="feedback-required">*</span>
                </label>
                <Dropdown
                  className="feedback-dropdown"
                  options={feedbackOptions}
                  value={feedbackType}
                  onChange={setFeedbackType}
                  placeholder="Select Feedback Type"
                />
                {feedbackType === 'mydata' && !authLoading && !user && (
                  <p className="feedback-note">
                    Deleting your Ask logs and bookmarks? Sign in first so we know which account is yours.
                  </p>
                )}

                <label className="feedback-label">
                  Description <span className="feedback-required">*</span>
                </label>
                <textarea
                  className="feedback-textarea"
                  placeholder={getPlaceholder()}
                  rows={4}
                  maxLength={MAX_DESCRIPTION}
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                />

                <label className="feedback-label">
                  Email {ACCOUNT_TYPES.includes(feedbackType)
                    ? <span className="feedback-required">*</span>
                    : '(Optional)'}
                </label>
                <input
                  className="feedback-input"
                  type="email"
                  maxLength={MAX_EMAIL}
                  placeholder="How should we contact you?"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                />

                <div ref={turnstileRef} className="feedback-turnstile" />

                <button className="feedback-submit" onClick={handleSubmit} disabled={loading || !turnstileToken}>
                  {loading ? 'Sending...' : 'Submit'}
                </button>
              </>
            )}
          </div>
        </div>
      )}
    </>
  );
};

export default FeedbackTab;