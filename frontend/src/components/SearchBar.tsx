import { useState, useEffect, useRef, useMemo } from 'react';
import { useNavigate, useLocation, Link } from 'react-router-dom';
import Dropdown from './Dropdown';
import { fetchSearchSuggestions, askChat } from '../api/api';
import type { SearchSuggestion, ChatResponse } from '../api/api';
import './SearchBar.css';

const searchOptions = [
  { value: 'Professor', label: 'Professor' },
  { value: 'Course', label: 'Course' },
  { value: 'Ask', label: 'Ask' },
];

const SearchBar = () => {
  const navigate = useNavigate();
  const location = useLocation();
  const [searchType, setSearchType] = useState('Professor');
  const [query, setQuery] = useState('');
  const [suggestions, setSuggestions] = useState<SearchSuggestion[]>([]);
  const [showSuggestions, setShowSuggestions] = useState(false);
  const [activeIndex, setActiveIndex] = useState(-1);
  const [isFocused, setIsFocused] = useState(false);
  const [placeholder, setPlaceholder] = useState('');
  const [askLoading, setAskLoading] = useState(false);
  const [askResult, setAskResult] = useState<ChatResponse | null>(null);

  const isAsk = searchType === 'Ask';

  const wrapperRef = useRef<HTMLDivElement>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const professorExamples = useMemo(() => [
    "Alan Mislove", "Ravi Sundaram", "Dan Felushko", "Cristina Nita-Rotaru",
    "Stacy Marsella", "Kathleen Durant", "Gene Cooperman", "Amit Shesh",
    "Mark Sheldon", "Nat Tuck", "Abhi Shelat", "Bryan Lackaye",
    "Predrag Radivojac", "Martin Schedlbauer", "Laney Strange",
    "Ben Hescott", "Rajmohan Rajaraman", "Keith Bagley",
    "Jonathan Meil", "Robert Platt", "Amal Ahmed", "Karl Lieberherr",
    "Frank Tip", "Jay McCarthy", "Christo Wilson",
    "Sami Rollins", "David Choffnes", "John Rachlin",
    "Alina Ene", "Pete Hartman", "Rose Yu", "Ji-Yong Shin",
    "Jonathan Ullman", "Stavros Tripakis", "Byron Wallace"
  ], []);

  const courseExamples = useMemo(() => [
    "CS 2500", "CS 3500", "CS 4500", "ECON 1115", "MATH 1341",
    "CY 2550", "PHYS 1161", "ACCT 1201", "MKTG 2101", "BIOL 1111",
    "CS 1800", "CS 2510", "CS 3000", "CS 3200", "CS 3650",
    "CS 3700", "CS 3800", "CS 4400", "CS 4530", "CS 4550",
    "ENGW 1111", "ENGW 3302", "MATH 1342", "MATH 2331", "MATH 3081",
    "PHYS 1151", "PHYS 1155", "CHEM 1161", "PSYC 1101", "HIST 1130",
    "EECE 2160", "EECE 2322", "DS 2000", "DS 2001", "DS 3000",
    "ARTF 1122", "MUSC 1201", "PHIL 1101", "ENVR 1101", "FINA 2201"
  ], []);

  const askExamples = useMemo(() => [
    // single professor / course
    "Is Guha hard?",
    "How are Mislove's lectures?",
    "Is Fundies 1 a lot of work?",
    "Does Abhi Shelat give hard exams?",
    "Is Rachlin an easy grader?",
    "Is CS 3000 worth taking?",
    "How tough is Discrete Structures?",
    "Does Felushko curve grades?",
    "Is Schedlbauer a good teacher?",
    "Is Networks hard with Choffnes?",
    "Should I take Theory of Computation?",
    "Is Operating Systems brutal?",
    "Is Software Engineering project-heavy?",
    // comparisons (two entities)
    "Mislove or Rachlin for CS 2500?",
    "Is CS 3500 harder than CS 3000?",
    "Shesh vs Cooperman — who's better?",
    "Should I take CS 3650 or CS 3700 first?",
    "Lieberherr or Tip for software engineering?",
    "Is MATH 1341 or MATH 1342 more work?",
    "Nita-Rotaru vs Choffnes for Networks?",
    // multi-entity / both at once
    "How do Rachlin and Felushko teach Fundies 1?",
    "Are CS 2500 and CS 2510 a big jump?",
    "Tell me about Ullman and Ene for Algorithms",
    // stats-targeting
    "What's Mislove's average rating?",
    "What's the difficulty score for CS 3000?",
    "What percent would take Abhi Shelat again?",
    "How does Rachlin's rating compare to the average?",
    "Which CS course has the highest rating?",
    "Is CS 3500's workload above average?",
    "What's Guha's would-take-again percentage?",
    // comments-targeting
    "What do students say about Felushko?",
    "What are the reviews like for CS 3650?",
    "What's the common complaint about CS 4500?",
    "Do reviews say Cooperman's exams are fair?",
    "What do comments say about Mislove's grading?",
    "Are there reviews about CS 2510's workload?",
    "What do students think of Schedlbauer's lectures?"
  ], []);

  // Typing animation logic
  useEffect(() => {
    if (isFocused || query) {
      setPlaceholder(
        isAsk ? 'Ask about a professor or course...'
        : searchType === 'Professor' ? 'Search by professor name...'
        : 'Search by course name or code...'
      );
      return;
    }

    const examples = isAsk ? askExamples : searchType === 'Professor' ? professorExamples : courseExamples;
    let currentExampleIndex = Math.floor(Math.random() * examples.length);
    let currentText = "";
    let isDeleting = false;
    let typingSpeed = 100;

    const type = () => {
      const fullText = examples[currentExampleIndex];

      if (isDeleting) {
        currentText = fullText.substring(0, currentText.length - 1);
        typingSpeed = 50;
      } else {
        currentText = fullText.substring(0, currentText.length + 1);
        typingSpeed = 100;
      }

      setPlaceholder(isAsk ? currentText : `Search for "${currentText}"`);

      if (!isDeleting && currentText === fullText) {
        isDeleting = true;
        typingSpeed = 2000; // Pause at end
      } else if (isDeleting && currentText === "") {
        isDeleting = false;
        currentExampleIndex = (currentExampleIndex + 1) % examples.length;
        typingSpeed = 500; // Pause before next
      }

      timeoutId = setTimeout(type, typingSpeed);
    };

    let timeoutId = setTimeout(type, typingSpeed);
    return () => clearTimeout(timeoutId);
  }, [isFocused, query, searchType, isAsk, professorExamples, courseExamples, askExamples]);

  const handleSearchTypeChange = (newType: string) => {
    setSearchType(newType);
    setQuery('');
    setSuggestions([]);
    setShowSuggestions(false);
    setAskResult(null);
    setAskLoading(false);
  };

  // Debounced fetch (search modes only — Ask costs LLM tokens, so it never fetches as-you-type)
  useEffect(() => {
    if (isAsk) return;
    if (debounceRef.current) clearTimeout(debounceRef.current);

    const trimmedQuery = query.trim();
    if (trimmedQuery.length < 2) {
      // Don't call setState here if it's already empty/false
      // But we need to handle it. Actually, the lint error is specific to SYNC calls in body.
      // We can use an async or just handle it.
      return;
    }

    debounceRef.current = setTimeout(async () => {
      try {
        const results = await fetchSearchSuggestions(trimmedQuery, searchType);
        const limitedResults = searchType === 'Professor' ? results.slice(0, 3) : results;
        setSuggestions(limitedResults);
        setShowSuggestions(limitedResults.length > 0);
        setActiveIndex(-1);
      } catch {
        setSuggestions([]);
        setShowSuggestions(false);
      }
    }, 200);

    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [query, searchType, isAsk]);

  // Handle query change to sync suggestions state
  const handleQueryChange = (val: string) => {
    setQuery(val);
    if (val.trim().length < 2) {
      setSuggestions([]);
      setShowSuggestions(false);
    }
  };

  // Ask submit: one self-contained question → one answer (single-shot, no history).
  const submitAsk = async () => {
    const q = query.trim();
    if (!q || askLoading) return;
    setAskResult(null);
    setAskLoading(true);
    setShowSuggestions(true);
    const { status, body } = await askChat(q);
    setAskLoading(false);
    setAskResult(body);
    if (status === 401) {
      // No sign-in modal lives here; mirror the navbar's open-feedback event pattern.
      window.dispatchEvent(new CustomEvent('open-signin'));
    }
  };

  // Close on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (wrapperRef.current && !wrapperRef.current.contains(e.target as Node)) {
        setShowSuggestions(false);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  // Removed redundant clear effect

  const handleSelect = (suggestion: SearchSuggestion) => {
    setShowSuggestions(false);
    const fromCatalog = location.pathname === '/professors'
      ? `${location.pathname}${location.search}`
      : undefined;
    if (suggestion.type === 'professor') {
      const slug = suggestion.slug;
      navigate(`/professors/${slug}`, { state: { fromCatalog } });
    } else {
      const code = suggestion.code.toLowerCase();
      navigate(`/courses/${code}`);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (isAsk) {
      if (e.key === 'Enter') {
        e.preventDefault();
        submitAsk();
      } else if (e.key === 'Escape') {
        setShowSuggestions(false);
      }
      return;
    }
    if (!showSuggestions || suggestions.length === 0) return;

    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setActiveIndex((prev) => {
        const next = prev < suggestions.length - 1 ? prev + 1 : 0;
        document.querySelector(`.suggestion-item:nth-child(${next + 1})`)?.scrollIntoView({ block: 'nearest' });
        return next;
      });
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setActiveIndex((prev) => {
        const next = prev > 0 ? prev - 1 : suggestions.length - 1;
        document.querySelector(`.suggestion-item:nth-child(${next + 1})`)?.scrollIntoView({ block: 'nearest' });
        return next;
      });
    } else if (e.key === 'Enter' && activeIndex >= 0) {
      e.preventDefault();
      handleSelect(suggestions[activeIndex]);
    } else if (e.key === 'Escape') {
      setShowSuggestions(false);
    }
  };

  return (
    <div className="search-wrapper" ref={wrapperRef}>
      <div className="search-bar">
        <div onMouseDown={() => setShowSuggestions(false)}>
          <Dropdown
            className="search-dropdown"
            options={searchOptions}
            value={searchType}
            onChange={handleSearchTypeChange}
          />
        </div>

        <div className="search-divider" />

        <span className="search-icon">
          <svg
            xmlns="http://www.w3.org/2000/svg"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.2"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <circle cx="11" cy="11" r="8" />
            <line x1="21" y1="21" x2="16.65" y2="16.65" />
          </svg>
        </span>

        <input
          className="search-input"
          type="text"
          placeholder={placeholder}
          value={query}
          onChange={(e) => handleQueryChange(e.target.value)}
          onFocus={() => {
            setIsFocused(true);
            if (isAsk ? (askResult || askLoading) : suggestions.length > 0) setShowSuggestions(true);
          }}
          onBlur={() => setIsFocused(false)}
          onKeyDown={handleKeyDown}
        />
      </div>

      {showSuggestions && !isAsk && (
        <ul className="search-suggestions">
          {suggestions.map((s, i) => (
            <li
              key={s.type === 'professor' ? s.name : s.code}
              className={`suggestion-item ${i === activeIndex ? 'active' : ''}`}
              onClick={() => handleSelect(s)}
              onMouseEnter={() => setActiveIndex(i)}
            >
              {s.type === 'professor' ? (
                <>
                  <div className="suggestion-main">
                    <span className="suggestion-name">{s.name}</span>
                    <span className="suggestion-dept">{s.dept}</span>
                  </div>
                  {s.rating !== null && (
                    <span className="suggestion-rating">{s.rating.toFixed(2)}</span>
                  )}
                </>
              ) : (
                <div className="suggestion-main">
                  <span className="suggestion-name">
                    <span className="suggestion-code">{s.code}</span>
                    {' '}{s.name}
                  </span>
                  <span className="suggestion-dept">{s.dept}</span>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}

      {showSuggestions && isAsk && (askLoading || askResult) && (
        <div className="ask-result">
          {askLoading ? (
            <p className="ask-thinking">Thinking…</p>
          ) : askResult ? (
            <AskResult result={askResult} />
          ) : null}
        </div>
      )}
    </div>
  );
};

function AskResult({ result }: { result: ChatResponse }) {
  if (result.mode === 'question') {
    // Fallback entity (used when a source has no per-source tag, e.g. old cached answers).
    const fallbackHref = result.course_code
      ? `/courses/${result.course_code.toLowerCase()}`
      : result.professor_slug
      ? `/professors/${result.professor_slug}`
      : null;
    // Show a source only if the answer text actually cites it with [N].
    const cited = result.sources.filter((s) => result.answer.includes(`[${s.source_id}]`));
    return (
      <>
        <p className="ask-answer">{result.answer}</p>
        {cited.length > 0 && (
          <ol className="ask-sources">
            {cited.map((s) => {
              const href = s.course_code
                ? `/courses/${s.course_code.toLowerCase()}`
                : s.professor_slug
                ? `/professors/${s.professor_slug}`
                : fallbackHref;
              return (
                <li key={s.source_id}>
                  {href ? (
                    <Link className="ask-source-link" to={href}>[{s.source_id}]</Link>
                  ) : (
                    <span className="ask-source-link">[{s.source_id}]</span>
                  )}
                  <span className="ask-source-snippet">{s.snippet}</span>
                </li>
              );
            })}
          </ol>
        )}
        <p className="ask-disclaimer">{result.disclaimer}</p>
      </>
    );
  }

  if (result.mode === 'disambiguation') {
    return <p className="ask-answer">{result.message}</p>;
  }

  if (result.mode === 'error') {
    return <p className="ask-answer">{result.message}</p>;
  }

  if (result.mode === 'course_list') {
    return (
      <>
        <p className="ask-answer">{result.answer}</p>
        {result.courses.length > 0 && (
          <ol className="ask-sources">
            {result.courses.map((c) => (
              <li key={c.code}>
                <Link className="ask-source-link" to={`/courses/${c.code.toLowerCase()}`}>{c.code}</Link>
                <span className="ask-source-snippet">
                  {c.name}{c.rating != null ? ` · ${c.rating.toFixed(1)}★` : ''}
                </span>
              </li>
            ))}
          </ol>
        )}
        <p className="ask-disclaimer">{result.disclaimer}</p>
      </>
    );
  }

  // out_of_scope | thin_data | keyword — banner/message + any keyword comments
  return (
    <>
      {(result.banner || result.message) && (
        <p className="ask-answer">{result.banner || result.message}</p>
      )}
      {result.comments.length > 0 && (
        <ol className="ask-sources">
          {result.comments.slice(0, 10).map((c, i) => {
            const com = c as {
              snippet?: string;
              link?: { type: 'professor' | 'course'; value: string } | null;
            };
            // [N] citation links to the matched professor/course profile; plain when none.
            const href = com.link
              ? com.link.type === 'course'
                ? `/courses/${com.link.value.toLowerCase()}`
                : `/professors/${com.link.value}`
              : null;
            const marker = `[${i + 1}]`;
            return (
              <li key={i}>
                {href ? (
                  <Link className="ask-source-link" to={href}>{marker}</Link>
                ) : (
                  <span className="ask-source-link">{marker}</span>
                )}
                <span className="ask-source-snippet">{com.snippet}</span>
              </li>
            );
          })}
        </ol>
      )}
    </>
  );
}

export default SearchBar;