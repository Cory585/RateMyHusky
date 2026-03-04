import { useState } from 'react';
import logo from '../assets/logo.jpg';
import Dropdown from './Dropdown';
import './FeedbackTab.css';

const feedbackOptions = [
  { value: 'bug', label: 'Bug Report' },
  { value: 'feature', label: 'Feature Request' },
  { value: 'general', label: 'General Feedback' },
];

const FeedbackTab = () => {
  const [isOpen, setIsOpen] = useState(false);
  const [feedbackType, setFeedbackType] = useState('');

  return (
    <>
      <button className="feedback-tab" onClick={() => setIsOpen(true)}>
        Feedback
      </button>

      {isOpen && (
        <div className="feedback-overlay" onClick={() => setIsOpen(false)}>
          <div className="feedback-modal" onClick={(e) => e.stopPropagation()}>
            <button className="feedback-close" onClick={() => setIsOpen(false)}>
              ×
            </button>

            <div className="feedback-mascot">
              <img src={logo} alt="RateMyHusky Mascot" className="feedback-mascot-img" />
            </div>

            <h2 className="feedback-title">Feedback Form</h2>
            <p className="feedback-subtitle">
              Found a bug? RateMyHusky's #1 fan? Let our devs know through this form.
            </p>

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

            <label className="feedback-label">
              Description <span className="feedback-required">*</span>
            </label>
            <textarea
              className="feedback-textarea"
              placeholder="Say more about bugs, suggestions, etc."
              rows={4}
            />

            <label className="feedback-label">
              Email (Optional)
            </label>
            <input
              className="feedback-input"
              type="email"
              placeholder="How should we contact you?"
            />

            <button className="feedback-submit">Submit</button>
          </div>
        </div>
      )}
    </>
  );
};

export default FeedbackTab;