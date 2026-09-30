import { useState, useEffect, useRef, useCallback, useMemo, useLayoutEffect } from 'react';
import { useParams, Link, useLocation } from 'react-router-dom';
import Footer from '../components/Footer';
import NotFound from './NotFound';

import Dropdown from '../components/Dropdown';
import StarRating from '../components/StarRating';
import RatingBar from '../components/RatingBar';
import Breadcrumbs from '../components/Breadcrumbs';
import Seo from '../components/Seo';
import { fetchProfessorFull, fetchDeptAvg } from '../api/api';
import type { ProfessorProfile, ProfessorReview, RedditMention, DeptAvg } from '../api/api';
import { isPinned, pinnedFirst } from '../utils/askPinMatch';
import BookmarkButton from '../components/BookmarkButton';
import neuIcon from '../assets/neu-circle-icon.png';
import './Professor.css';

/* ───────── animated number counter ───────── */
const AnimatedNumber = ({
  value, decimals = 2, suffix = '',
}: { value: number | null; decimals?: number; suffix?: string }) => {
  const [display, setDisplay] = useState(value === null ? '—' : '0' + suffix);
  const hasAnimated = useRef(false);
  const prevValue = useRef<number | null>(null);
  const ref = useRef<HTMLSpanElement>(null);

  const animate = useCallback((from: number, to: number) => {
    const duration = 1000;
    const start = performance.now();
    const step = (now: number) => {
      const t = Math.min((now - start) / duration, 1);
      const eased = 1 - Math.pow(1 - t, 3);
      const current = from + (to - from) * eased;
      setDisplay(current.toFixed(decimals) + suffix);
      if (t < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }, [decimals, suffix]);

  useEffect(() => {
    if (value === null) {
      prevValue.current = null;
      return;
    }
    if (!hasAnimated.current) return;
    if (prevValue.current !== value) {
      animate(prevValue.current || 0, value);
      prevValue.current = value;
    }
  }, [value, animate]);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const obs = new IntersectionObserver(([e]) => { 
      if (e.isIntersecting && !hasAnimated.current && value !== null) { 
        hasAnimated.current = true;
        animate(0, value);
        prevValue.current = value;
        obs.disconnect(); 
      } 
    }, { threshold: 0.5 });
    obs.observe(el);
    return () => obs.disconnect();
  }, [animate, value]);

  return <span ref={ref}>{display}</span>;
};

/* ───────── sort / filter options ───────── */
const sortOptions = [
  { value: 'newest', label: 'Newest First' },
  { value: 'oldest', label: 'Oldest First' },
  { value: 'highest', label: 'Highest Rated' },
  { value: 'lowest', label: 'Lowest Rated' },
];

const redditSentimentOptions = [
  { value: 'all', label: 'All Sentiment' },
  { value: 'positive', label: 'Positive' },
  { value: 'neutral', label: 'Neutral' },
  { value: 'negative', label: 'Negative' },
];

const formatReviewDate = (dateStr: string) => {
  if (!dateStr) return '';
  // Convert "2022-12-11 18:36:58 +0000 UTC" to "2022-12-11T18:36:58Z"
  const normalized = dateStr.replace(' +0000 UTC', '').replace(' ', 'T') + 'Z';
  const date = new Date(normalized);
  if (isNaN(date.getTime())) return dateStr;
  return date.toLocaleDateString('en-US', {
    month: 'long',
    day: 'numeric',
    year: 'numeric'
  });
};

const GRADE_ORDER = ['A+','A','A-','B+','B','B-','C+','C','C-','D+','D','D-','F','W','WF','P','NP','I'];
const GRADE_COLORS: Record<string, string> = {
  'A+':'#1a9850','A':'#27ae60','A-':'#66bd63',
  'B+':'#a6d96a','B':'#d4e858','B-':'#fee08b',
  'C+':'#fdae61','C':'#f39c12','C-':'#e67e22',
  'D+':'#e74c3c','D':'#d73027','D-':'#c0392b',
  'F':'#a50026','W':'#7f8c8d','WF':'#636e72','P':'#2980b9','NP':'#8e44ad','I':'#999',
};

/* ───────── near-duplicate detection ───────── */
function normalizeText(s: string): string {
  return s.toLowerCase().replace(/\s+/g, ' ').trim();
}

function deduplicateByText<T>(items: T[], getText: (item: T) => string): T[] {
  const seen = new Set<string>();
  const result: T[] = [];
  for (const item of items) {
    const raw = getText(item);
    if (!raw.trim()) { result.push(item); continue; }
    // Use a truncated normalized form as a fingerprint — catches exact and near-exact dupes
    const norm = normalizeText(raw);
    // Check exact match first
    if (seen.has(norm)) continue;
    // Check prefix-based match (catches 95%+ similar: same text with minor trailing differences)
    const prefix = norm.slice(0, Math.floor(norm.length * 0.9));
    let isDupe = false;
    for (const s of seen) {
      if (s.startsWith(prefix) || norm.startsWith(s.slice(0, Math.floor(s.length * 0.9)))) {
        // Confirm length similarity (within 10%)
        const ratio = Math.min(s.length, norm.length) / Math.max(s.length, norm.length);
        if (ratio >= 0.9) { isDupe = true; break; }
      }
    }
    if (!isDupe) {
      seen.add(norm);
      result.push(item);
    }
  }
  return result;
}

/* ═══════════════════════════════════════ */
const Professor = () => {
  const { slug } = useParams<{ slug: string }>();
  const reviewsRef = useRef<HTMLElement>(null);
  const chartsRef = useRef<HTMLElement>(null);
  const gradesRef = useRef<HTMLDivElement>(null);
  const reviewTabsRef = useRef<HTMLDivElement>(null);

  const [profile, setProfile] = useState<ProfessorProfile | null>(null);
  const [reviews, setReviews] = useState<ProfessorReview[]>([]);
  const [deptAvg, setDeptAvg] = useState<DeptAvg | null>(null);
  const [redditMentions, setRedditMentions] = useState<RedditMention[]>([]);
  const [redditSentiment, setRedditSentiment] = useState('all');
  const [redditSearch, setRedditSearch] = useState('');
  const [visibleRedditMentions, setVisibleRedditMentions] = useState(10);
  const [loading, setLoading] = useState(true);
  const [reviewsLoading, setReviewsLoading] = useState(true);
  const [error, setError] = useState('');
  const [reviewTab, setReviewTab] = useState<'rmp' | 'reddit'>('rmp');
  const [sortBy, setSortBy] = useState('newest');
  const [visibleReviews, setVisibleReviews] = useState(10);
  const [selectedCourses, setSelectedCourses] = useState<Set<string>>(new Set());
  const [showBackToTop, setShowBackToTop] = useState(false);
  const [gradesAnimated, setGradesAnimated] = useState(false);
  const [reviewPillStyle, setReviewPillStyle] = useState({ left: 0, width: 0, opacity: 0 });
  const [isReviewPillReady, setIsReviewPillReady] = useState(false);
  const [showAllCourses, setShowAllCourses] = useState(false);
  const COURSES_COLLAPSED_LIMIT = 5;
  const [isImageModalOpen, setIsImageModalOpen] = useState(false);
const [showCourseTip, setShowCourseTip] = useState(() => localStorage.getItem('prof_course_tip_dismissed') !== '1');

  const location = useLocation();
  // Ask pins arrive via navigation state from a clicked citation. Keyed by askedAt so a new
  // question replaces old pins; normal navigation (no askPins) leaves this null.
  const [pinnedSources, setPinnedSources] = useState<{ source: string | null; snippet: string }[]>(
    () => {
      const st = location.state as { askPins?: { sources: { source: string | null; snippet: string }[] } } | null;
      return st?.askPins?.sources ?? [];
    }
  );
  // Sentinel 0 (never a real Date.now() askedAt) so the apply-pins effect's "already applied"
  // guard is false on first mount and actually performs the tab-switch + scroll. The effect
  // sets this ref to the real askedAt once it applies.
  const pinnedAskedAt = useRef<number>(0);
  // Tracks the askedAt we have already scrolled for, so the scroll fires exactly once per new
  // pin set — even though the apply-guard above trips on the effect's second run.
  const scrolledAskedAt = useRef<number>(0);

  /* ── review pill ── */
  const updateReviewPill = useCallback(() => {
    if (!reviewTabsRef.current) return;
    const activeTab = reviewTabsRef.current.querySelector('.prof-review-tab.active') as HTMLElement;
    if (activeTab) {
      setReviewPillStyle({
        left: activeTab.offsetLeft,
        width: activeTab.offsetWidth,
        opacity: 1
      });
    }
  }, []);

  useLayoutEffect(() => {
    if (!loading) {
      updateReviewPill();
    }
  }, [reviewTab, updateReviewPill, loading]);

  useEffect(() => {
    const container = reviewTabsRef.current;
    if (!container || loading) return;

    updateReviewPill();
    const timer = setTimeout(() => setIsReviewPillReady(true), 150);

    const observer = new ResizeObserver(() => {
      setIsReviewPillReady(false);
      updateReviewPill();
      setTimeout(() => setIsReviewPillReady(true), 50);
    });

    observer.observe(container);
    return () => {
      clearTimeout(timer);
      observer.disconnect();
    };
  }, [updateReviewPill, loading]);

  /* ── profile loading ── */
  /* ── combined profile + reviews load (single round-trip) ── */
  useEffect(() => {
    if (!slug) {
      setLoading(false);
      setError('Professor not found.');
      return;
    }
    let cancelled = false;
    async function load() {
      setLoading(true); setReviewsLoading(true); setError('');
      try {
        const data = await fetchProfessorFull(slug!);
        if (cancelled) return;
        if (!data) {
          setError('Professor not found.');
        } else {
          setProfile(data);
          setReviews(data.reviews || []);
          setRedditMentions(data.redditMentions || []);
        }
      } catch { if (!cancelled) setError('Failed to load professor data.'); }
      finally { if (!cancelled) { setLoading(false); setReviewsLoading(false); } }
    }
    load();
    return () => { cancelled = true; };
  }, [slug]);

  /* ── department averages for the comparison card ── */
  useEffect(() => {
    if (!profile?.department) return;
    let cancelled = false;
    fetchDeptAvg(profile.department)
      .then(d => { if (!cancelled) setDeptAvg(d); })
      .catch(() => { if (!cancelled) setDeptAvg(null); });
    return () => { cancelled = true; };
  }, [profile?.department]);

  /* ── Ask citation pins: switch to the cited source's tab and scroll to reviews ── */
  useEffect(() => {
    const st = location.state as {
      askPins?: {
        askedAt: number;
        clicked: { source: string | null; snippet: string };
        sources: { source: string | null; snippet: string }[];
      };
    } | null;
    const pins = st?.askPins;
    if (!pins) {
      // Arrived without pins (normal nav / breadcrumb / slug change) — clear any stale pins so
      // a previous question's sources don't ride along and mis-pin rows on this page.
      if (pinnedSources.length > 0) {
        setPinnedSources([]);
        pinnedAskedAt.current = 0;
        scrolledAskedAt.current = 0;
      }
      return;
    }

    // Apply the pins + tab switch once per new askedAt.
    if (pins.askedAt !== pinnedAskedAt.current) {
      pinnedAskedAt.current = pins.askedAt;
      setPinnedSources(pins.sources);
      const src = pins.clicked.source;
      const tab: 'rmp' | 'reddit' = src === 'rmp' ? 'rmp' : 'reddit';
      setReviewTab(tab);
    }

    // Scroll once per askedAt, but only after reviews have rendered (reviewsLoading false).
    // On first mount reviewsLoading starts true; this effect re-runs when it flips false
    // (it's in the deps) and the scroll fires then.
    if (!reviewsLoading && scrolledAskedAt.current !== pins.askedAt) {
      scrolledAskedAt.current = pins.askedAt;
      setTimeout(() => reviewsRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 150);
    }
  }, [location.state, reviewsLoading]);

  /* ── back to top ── */
  useEffect(() => {
    const handler = () => setShowBackToTop(window.scrollY > 300);
    window.addEventListener('scroll', handler, { passive: true });
    return () => window.removeEventListener('scroll', handler);
  }, []);

  /* ── logic ── */
  const courseCodeMap = useMemo(() => {
    const map = new Map<string, string>();
    profile?.courses?.forEach((c) => {
      const numMatch = c.code.match(/\d+/);
      map.set(c.code, c.code);
      if (numMatch) map.set(numMatch[0], c.code);
    });
    return map;
  }, [profile]);

  const getFormattedCourseCode = useCallback((input: string) => {
    if (!input) return '';
    const clean = input.replace(/\s+/g, '').toUpperCase();
    if (courseCodeMap.has(clean)) return courseCodeMap.get(clean)!;
    const match = clean.match(/\d+/);
    if (match && courseCodeMap.has(match[0])) return courseCodeMap.get(match[0])!;
    return clean;
  }, [courseCodeMap]);

  const allCourseCodes = useMemo(() => {
    const codes = new Set<string>();
    profile?.courses?.forEach(c => codes.add(c.code));
    reviews.forEach(r => {
      const code = getFormattedCourseCode(r.course);
      if (code) codes.add(code.toUpperCase());
    });
    return Array.from(codes).sort();
  }, [profile, reviews, getFormattedCourseCode]);

  const hasInitializedSelection = useRef(false);
  useEffect(() => {
    if (allCourseCodes.length === 0) return;
    if (!hasInitializedSelection.current) {
      setSelectedCourses(new Set(allCourseCodes));
      hasInitializedSelection.current = true;
    } else {
      // Merge any new course codes from reviews into the existing selection
      setSelectedCourses(prev => {
        const next = new Set(prev);
        let changed = false;
        allCourseCodes.forEach(c => { if (!next.has(c)) { next.add(c); changed = true; } });
        return changed ? next : prev;
      });
    }
  }, [allCourseCodes]);

  /* Every stored RMP rating for the selected courses. Counts and the rating
     distribution read this, not the deduplicated list below: the text dedupe is
     a display rule for the review cards, and using it as a count made the stat
     cards disagree with the leaderboard, which counts rating rows. */
  const rmpRatingsInSelection = useMemo(
    () => reviews.filter(r => selectedCourses.has(getFormattedCourseCode(r.course).toUpperCase())),
    [reviews, selectedCourses, getFormattedCourseCode]);

  const filteredRmpReviews = useMemo(
    () => deduplicateByText(rmpRatingsInSelection, r => r.comment),
    [rmpRatingsInSelection]);

  const stats = useMemo(() => {
    if (!profile) return null;

    const noneSelected = selectedCourses.size === 0;
    const allSelected = allCourseCodes.length > 0 && selectedCourses.size === allCourseCodes.length;

    if (noneSelected) {
      return {
        avgRating: null,
        difficulty: null,
        totalRatings: null,
        wouldTakeAgainPct: profile.wouldTakeAgainPct,
      };
    }

    if (allSelected) {
      return {
        avgRating: profile.avgRating,
        difficulty: profile.difficulty ?? 0,
        /* The catalog's own count — the same field the GOATED board's "Ratings"
           column serves, so the two pages agree by construction instead of by
           two hand-rolled sums happening to land on the same number. */
        totalRatings: profile.totalRatings,
        wouldTakeAgainPct: profile.wouldTakeAgainPct,
      };
    }

    // Course-filtered: recomputed over every rating in the selection, the same
    // rows the count below reports — a mean over the text-deduplicated subset
    // would be a mean of one population beside the size of another. null, not 0,
    // for a selection with no ratings: 0 would render as 0.00 under five empty stars.
    const avgRating = rmpRatingsInSelection.length > 0
      ? rmpRatingsInSelection.reduce((acc, r) => acc + r.quality, 0) / rmpRatingsInSelection.length
      : null;
    const rmpDifficulty = rmpRatingsInSelection.length > 0
      ? rmpRatingsInSelection.reduce((acc, r) => acc + r.difficulty, 0) / rmpRatingsInSelection.length
      : null;

    return {
      avgRating,
      difficulty: rmpDifficulty ?? profile.difficulty ?? 0,
      totalRatings: rmpRatingsInSelection.length,
      wouldTakeAgainPct: profile.wouldTakeAgainPct,
    };
  }, [profile, rmpRatingsInSelection, allCourseCodes, selectedCourses]);

  const ratingDistribution = useMemo(() => {
    const counts = { 1: 0, 2: 0, 3: 0, 4: 0, 5: 0 };

    if (selectedCourses.size === 0) {
      return [5, 4, 3, 2, 1].map(star => ({ star, count: 0 }));
    }

    // Every rating, so the bars add up to the Total Ratings card above
    rmpRatingsInSelection.forEach(r => {
      if (r.quality >= 1 && r.quality <= 5) {
        const q = Math.round(r.quality) as 1 | 2 | 3 | 4 | 5;
        counts[q]++;
      }
    });

    return [5, 4, 3, 2, 1].map(star => ({
      star,
      count: counts[star as 1 | 2 | 3 | 4 | 5],
    }));
  }, [rmpRatingsInSelection, selectedCourses]);

  const maxCount = useMemo(() => Math.max(...ratingDistribution.map(d => d.count), 1), [ratingDistribution]);

  const gradeDistribution = useMemo(() => {
    const counts: Record<string, number> = {};
    filteredRmpReviews.forEach(r => {
      const g = r.grade?.trim();
      if (g && g !== 'N/A' && g !== 'Not sure yet' && g !== 'Rather not say') {
        counts[g] = (counts[g] || 0) + 1;
      }
    });
    const total = Object.values(counts).reduce((a, b) => a + b, 0);
    if (total === 0) return [];
    // Find the range of grades that appear and include all in-between
    const presentIndices = GRADE_ORDER.map((g, i) => counts[g] ? i : -1).filter(i => i >= 0);
    const minIdx = Math.min(...presentIndices);
    const maxIdx = Math.max(...presentIndices);
    return GRADE_ORDER.slice(minIdx, maxIdx + 1).map(g => ({
      grade: g,
      count: counts[g] || 0,
      pct: ((counts[g] || 0) / total) * 100,
      color: GRADE_COLORS[g] || '#999'
    }));
  }, [filteredRmpReviews]);

  useEffect(() => {
    const el = gradesRef.current;
    if (!el) return;
    const obs = new IntersectionObserver(([e]) => {
      if (e.isIntersecting) {
        setGradesAnimated(true);
        obs.disconnect();
      }
    }, { threshold: 0.3 });
    obs.observe(el);
    return () => obs.disconnect();
  }, [profile, gradeDistribution.length]);

  const pinSnippets = useMemo(() => ({
    rmp: pinnedSources.filter((p) => p.source === 'rmp').map((p) => p.snippet),
    // sources with null/unknown source came from Reddit historically (see SOURCE_LABEL default)
    reddit: pinnedSources.filter((p) => p.source === 'reddit' || p.source == null).map((p) => p.snippet),
  }), [pinnedSources]);

  const sortedReviews = useMemo(() => {
    const sorted = [...filteredRmpReviews].sort((a, b) => {
      if (sortBy === 'oldest') return new Date(a.date).getTime() - new Date(b.date).getTime();
      if (sortBy === 'highest') return b.quality - a.quality;
      if (sortBy === 'lowest') return a.quality - b.quality;
      return new Date(b.date).getTime() - new Date(a.date).getTime();
    });
    return pinnedFirst(sorted, (r) => r.comment || '', pinSnippets.rmp);
  }, [filteredRmpReviews, sortBy, pinSnippets.rmp]);

  const redditQuery = redditSearch.trim().toLowerCase();
  const filteredRedditMentions = useMemo(() => {
    const filtered = redditMentions.filter(m =>
      (redditSentiment === 'all' || m.sentiment === redditSentiment) &&
      (redditQuery === '' || m.body.toLowerCase().includes(redditQuery))
    );
    return pinnedFirst(filtered, (m) => m.body || '', pinSnippets.reddit);
  }, [redditMentions, redditSentiment, redditQuery, pinSnippets.reddit]);

  const formatRedditDate = (utc: string | null): string => {
    if (!utc) return '';
    const d = new Date(utc);
    if (isNaN(d.getTime())) return '';
    return d.toLocaleDateString('en-US', { month: 'long', day: 'numeric', year: 'numeric' });
  };

  const sentimentBorderColor = (s: RedditMention['sentiment']): string =>
    s === 'positive' ? '#27ae60' : s === 'negative' ? '#e74c3c' : s === 'neutral' ? '#f39c12' : '#95a5a6';

  // Reddit permalinks are site-relative (e.g. "/r/NEU/comments/..."); allow those and
  // absolute http(s) URLs only, blocking javascript:/data: and other schemes.
  const safeRedditUrl = (url: string | null): string | null => {
    if (!url) return null;
    if (url.startsWith('/')) return `https://www.reddit.com${url}`;
    try {
      return ['http:', 'https:'].includes(new URL(url).protocol) ? url : null;
    } catch { return null; }
  };

  const toggleCourse = (code: string) => {
    setSelectedCourses(prev => {
      const next = new Set(prev);
      if (next.has(code)) next.delete(code);
      else next.add(code);
      return next;
    });
  };

  useEffect(() => { setVisibleReviews(10); }, [sortBy, reviewTab]);
  // Reset visible reviews when course filter changes, but only if the filtered list got smaller
  useEffect(() => { setVisibleReviews(v => Math.min(v, Math.max(10, filteredRmpReviews.length))); }, [selectedCourses.size, filteredRmpReviews.length]);

  useEffect(() => {
    if (!isImageModalOpen) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setIsImageModalOpen(false);
    };
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    window.addEventListener('keydown', onKeyDown);
    return () => {
      document.body.style.overflow = prevOverflow;
      window.removeEventListener('keydown', onKeyDown);
    };
  }, [isImageModalOpen]);

  if (loading) return (
    <div className="prof-page">
      <div className="prof-loading">
        <div className="prof-loading-spinner" />
        <p>Loading professor data…</p>
      </div>
    </div>
  );

  if (error || !profile || !stats) return <NotFound />;

  /* Mirrors render.professor_html's two forms deliberately: that module serves
     this same page to crawlers, and a professor whose description differs
     between the two renders is the kind of mismatch that costs the canonical. */
  const seoDescription = profile.avgRating !== null
    ? `${profile.name} professor reviews and ratings: ${profile.avgRating.toFixed(1)}/5 from ${profile.totalRatings} student ratings at Northeastern` +
      (profile.wouldTakeAgainPct != null ? ` (${profile.wouldTakeAgainPct}% would take again)` : '') +
      `. RateMyProfessors + Reddit.`
    : `${profile.name}, Northeastern ${profile.department} professor: no student ratings yet. RateMyProfessors + Reddit.`;
  const profCanonical = `https://ratemyhusky.com/professors/${slug}`;
  const profJsonLd = {
    '@context': 'https://schema.org',
    '@type': 'ProfilePage',
    dateModified: new Date().toISOString().slice(0, 10),
    mainEntity: {
      '@type': 'Person',
      name: profile.name,
      jobTitle: 'Professor',
      worksFor: {
        '@type': 'CollegeOrUniversity',
        name: 'Northeastern University',
        sameAs: 'https://www.northeastern.edu',
      },
      knowsAbout: profile.department,
      url: profCanonical,
      ...(profile.imageUrl ? { image: profile.imageUrl } : {}),
      ...(profile.professorUrl ? { sameAs: [profile.professorUrl] } : {}),
      // schema.org Person does not support aggregateRating (Google rejects it
      // as an invalid object type in Rich Results), so it is intentionally omitted.
    },
  };
  const profBreadcrumbJsonLd = {
    '@context': 'https://schema.org',
    '@type': 'BreadcrumbList',
    itemListElement: [
      { '@type': 'ListItem', position: 1, name: 'Home', item: 'https://ratemyhusky.com/' },
      { '@type': 'ListItem', position: 2, name: 'Professors', item: 'https://ratemyhusky.com/professors' },
      { '@type': 'ListItem', position: 3, name: profile.name, item: profCanonical },
    ],
  };

  return (
    <div className="prof-page">
      <Seo
        title={`${profile.name} Reviews & Ratings — Northeastern ${profile.department}`}
        description={seoDescription}
        canonical={profCanonical}
        image={profile.imageUrl}
        ogType="profile"
        jsonLd={[profJsonLd, profBreadcrumbJsonLd]}
      />
      <header className="prof-hero">
        <div className="prof-hero-bg" style={{ backgroundImage: `url(${neuIcon})` }} />
        <div className="prof-hero-glow" />
        <Breadcrumbs items={[
          { label: 'Professors', to: '/professors' },
          { label: profile.name },
        ]} />
        <div className="prof-hero-inner">
          <div
            className={`prof-avatar ${profile.imageUrl ? 'prof-avatar-clickable' : ''}`}
            onClick={() => {
              if (profile.imageUrl) setIsImageModalOpen(true);
            }}
            role={profile.imageUrl ? 'button' : undefined}
            tabIndex={profile.imageUrl ? 0 : undefined}
            onKeyDown={(e) => {
              if (!profile.imageUrl) return;
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                setIsImageModalOpen(true);
              }
            }}
            aria-label={profile.imageUrl ? `Open larger photo of ${profile.name}` : undefined}
          >
            {profile.imageUrl ? (
              <img
                src={profile.imageUrl}
                alt={profile.name}
                className="prof-avatar-img"
                style={{ objectPosition: `${profile.focusX ?? 50}% ${profile.focusY ?? 30}%` }}
                onError={(e) => {
                  const target = e.currentTarget;
                  target.style.display = 'none';
                  const initials = target.parentElement?.querySelector('.prof-avatar-initials') as HTMLElement;
                  if (initials) initials.style.display = 'flex';
                }}
              />
            ) : null}
            <span
              className="prof-avatar-initials"
              style={profile.imageUrl ? { display: 'none' } : undefined}
            >
              {profile.name.split(' ').map(n => n[0]).join('')}
            </span>
          </div>
          <div className="prof-hero-info">
            <h1 className="prof-name">
              {profile.name}
              <BookmarkButton itemType="professor" itemKey={slug!} size="md" className="prof-hero-bookmark" />
            </h1>
            <p className="prof-dept">{profile.department}</p>
          </div>
        </div>
      </header>

      <section className="prof-stats">
        <div className="prof-stat-card">
          <span className="prof-stat-value">{stats.avgRating !== null ? <AnimatedNumber value={stats.avgRating} /> : '—'}</span>
          <span className="prof-stat-label">Overall Rating</span>
          <StarRating rating={stats.avgRating ?? 0} size="lg" />
        </div>
        <div className="prof-stat-card">
          <span className="prof-stat-value">{stats.difficulty != null && stats.difficulty > 0 ? <AnimatedNumber value={stats.difficulty} /> : '—'}</span>
          <span className="prof-stat-label">Difficulty</span>
          <div className="prof-difficulty-bar">
            <div className="prof-difficulty-fill" style={{ 
              width: `${((stats.difficulty ?? 0) / 5) * 100}%`,
              background: (() => {
                const d = stats.difficulty ?? 0;
                if (d <= 1.5) return '#27ae60';
                if (d <= 2.5) return '#66bd63';
                if (d <= 3.0) return '#f39c12';
                if (d <= 3.5) return '#e67e22';
                if (d <= 4.0) return '#e74c3c';
                return '#c0392b';
              })()
            }} />
          </div>
        </div>
        <div className="prof-stat-card">
          <span className={`prof-stat-value ${stats.wouldTakeAgainPct !== null ? 'green' : ''}`}>
            {stats.wouldTakeAgainPct !== null ? <AnimatedNumber value={stats.wouldTakeAgainPct} decimals={0} suffix="%" /> : '—'}
          </span>
          <span className="prof-stat-label">Would Take Again</span>
        </div>
        <div className="prof-stat-card prof-stat-clickable" onClick={() => chartsRef.current?.scrollIntoView({ behavior: 'smooth' })}>
          <span className="prof-stat-value">{stats.totalRatings ? stats.totalRatings.toLocaleString() : '—'}</span>
          <span className="prof-stat-label">Total Ratings</span>
          <span className="prof-stat-hint">View distribution ↓</span>
        </div>
        <div className="prof-stat-card prof-stat-clickable" onClick={() => reviewsRef.current?.scrollIntoView({ behavior: 'smooth' })}>
          <span className="prof-stat-value">{filteredRmpReviews.length.toLocaleString()}</span>
          <span className="prof-stat-label">Total Comments</span>
          <span className="prof-stat-hint">Read reviews ↓</span>
        </div>
      </section>

      <div className="prof-hero-actions-row">
        <Link 
          to={`/compare?a=${profile.name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '')}`} 
          className="prof-compare-btn"
        >
          Compare
        </Link>
        {profile.professorUrl && (
          <a href={profile.professorUrl} target="_blank" rel="noreferrer" className="prof-rmp-btn">
            View on RMP →
          </a>
        )}
      </div>

      <section className="prof-section prof-charts-row" ref={chartsRef}>
        <div className="prof-chart-card">
          <h3 className="prof-chart-title">Rating Distribution</h3>
          <div className="prof-distribution">
            {ratingDistribution.map((d) => (
              <RatingBar key={d.star} star={d.star} count={d.count} max={maxCount} />
            ))}
          </div>
        </div>
        {gradeDistribution.length > 0 && (
          <div className="prof-chart-card" ref={gradesRef}>
            <h3 className="prof-chart-title">Grade Distribution</h3>
            <div className="prof-grades">
              {gradeDistribution.map((g) => (
                <div key={g.grade} className="prof-grade-row">
                  <span className="prof-grade-label" style={{ color: g.color }}>{g.grade}</span>
                  <div className="prof-grade-track">
                    <div className="prof-grade-fill" style={{ width: gradesAnimated ? `${g.pct}%` : '0%', background: g.color }} />
                  </div>
                  <span className="prof-grade-count">{g.count}</span>
                </div>
              ))}
            </div>
          </div>
        )}
        {deptAvg && (deptAvg.avgRating != null || deptAvg.difficulty != null || deptAvg.wouldTakeAgainPct != null) && (
          <div className="prof-chart-card">
            <h3 className="prof-chart-title">vs. {profile.department}</h3>
            <div className="prof-compare-rows">
              {([
                { label: 'Rating', prof: profile.avgRating, dept: deptAvg.avgRating, max: 5, fmt: (v: number) => v.toFixed(1) },
                { label: 'Difficulty', prof: profile.difficulty, dept: deptAvg.difficulty, max: 5, fmt: (v: number) => v.toFixed(1) },
                { label: 'Would Take Again', prof: profile.wouldTakeAgainPct, dept: deptAvg.wouldTakeAgainPct, max: 100, fmt: (v: number) => `${Math.round(v)}%` },
              ]).map(m => (
                <div key={m.label} className="prof-compare-row">
                  <span className="prof-compare-label">{m.label}</span>
                  {([['This professor', m.prof, ''], ['Department', m.dept, ' dept']] as const).map(([who, v, cls]) => (
                    <div key={who} className="prof-compare-bar">
                      <span className="prof-compare-who">{who}</span>
                      <div className="prof-compare-track">
                        {v != null && <div className={`prof-compare-fill${cls}`} style={{ width: `${Math.min(100, (v / m.max) * 100)}%` }} />}
                      </div>
                      <span className="prof-compare-val">{v != null ? m.fmt(v) : '—'}</span>
                    </div>
                  ))}
                </div>
              ))}
            </div>
          </div>
        )}
      </section>

      {isImageModalOpen && profile.imageUrl && (
        <div className="prof-image-modal-overlay" onClick={() => setIsImageModalOpen(false)}>
          <div className="prof-image-modal" onClick={(e) => e.stopPropagation()}>
            <button
              className="prof-image-modal-close"
              onClick={() => setIsImageModalOpen(false)}
              aria-label="Close enlarged professor image"
            >
              ×
            </button>
            <img src={profile.imageUrl} alt={profile.name} className="prof-image-modal-img" />
          </div>
        </div>
      )}

      {allCourseCodes.length > 0 && (() => {
        const known = new Set(profile.courses.map(c => c.code));
        // Codes that only appear on review rows have no course page, so they list after the profile's courses.
        const reviewOnly = allCourseCodes.filter(code => !known.has(code));
        const rows = [
          ...profile.courses.map(c => ({ code: c.code, name: c.name, numReviews: c.numReviews, avgRating: c.avgRating, hasPage: true })),
          ...reviewOnly.map(code => ({ code, name: null, numReviews: null, avgRating: null, hasPage: false })),
        ];
        const renderRow = (row: typeof rows[number]) => (
          <div
            key={row.code}
            className={`prof-course-row ${selectedCourses.has(row.code) ? 'selected' : ''}`}
            onClick={() => toggleCourse(row.code)}
          >
            <div className="prof-course-row-main">
              <span className="prof-course-code">{row.code}</span>
              <span className="prof-course-title">{row.name || ''}</span>
              <span className="prof-course-terms">
                {row.numReviews != null
                  ? `${row.numReviews} review${row.numReviews === 1 ? '' : 's'}${row.avgRating != null ? ` · ${row.avgRating.toFixed(1)}` : ''}`
                  : 'Reviews only'}
              </span>
              {row.hasPage && (
                <Link
                  to={`/courses/${row.code.toLowerCase()}`}
                  state={{ fromPage: { label: profile.name, url: `/professors/${slug}` } }}
                  className="prof-course-view-btn"
                  onClick={(e) => e.stopPropagation()}
                >
                  View Course
                </Link>
              )}
            </div>
          </div>
        );
        return (
          <section className="prof-section">
            <div className="prof-section-header">
              <h2 className="prof-section-title">Courses Taught</h2>
              <div className="prof-section-actions">
                <button className="prof-action-link" onClick={() => setSelectedCourses(new Set(allCourseCodes))}>Select All</button>
                <button className="prof-action-link" onClick={() => setSelectedCourses(new Set())}>Clear All</button>
              </div>
            </div>
            <div className="prof-courses-compact">
              {rows.slice(0, COURSES_COLLAPSED_LIMIT).map(renderRow)}
              {rows.length > COURSES_COLLAPSED_LIMIT && (
                <>
                  <div className={`prof-courses-extra ${showAllCourses ? 'open' : ''}`}>
                    <div>{rows.slice(COURSES_COLLAPSED_LIMIT).map(renderRow)}</div>
                  </div>
                  <button
                    className="prof-courses-toggle"
                    onClick={() => setShowAllCourses(v => !v)}
                  >
                    {showAllCourses ? 'Show fewer' : `+${rows.length - COURSES_COLLAPSED_LIMIT} more courses`}
                    <svg
                      className={`prof-courses-toggle-icon ${showAllCourses ? 'expanded' : ''}`}
                      width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"
                    >
                      <polyline points="6 9 12 15 18 9" />
                    </svg>
                  </button>
                </>
              )}
            </div>
          </section>
        );
      })()}

      {showCourseTip && allCourseCodes.length > 0 && (
        <div className="prof-course-tip-wrapper">
          <div className="prof-course-tip">
            <div className="prof-course-tip-icon">
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="12" cy="12" r="10" />
                <line x1="12" y1="8" x2="12" y2="12" />
                <line x1="12" y1="16" x2="12.01" y2="16" />
              </svg>
            </div>
            <div className="prof-course-tip-body">
              <div className="prof-course-tip-label">Tip</div>
              <p className="prof-course-tip-text">
                To filter reviews by course, click <strong>Clear All</strong> in the Courses Taught section, then select the course you want to see reviews for.
              </p>
            </div>
            <button className="prof-course-tip-close" onClick={() => { localStorage.setItem('prof_course_tip_dismissed', '1'); setShowCourseTip(false); }} aria-label="Dismiss tip">
              <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <line x1="18" y1="6" x2="6" y2="18" />
                <line x1="6" y1="6" x2="18" y2="18" />
              </svg>
            </button>
          </div>
        </div>
      )}

      <section className="prof-section prof-reviews-section" ref={reviewsRef}>
        <div className="prof-reviews-header">
          <h2 className="prof-section-title">Reviews</h2>
          <div className="prof-review-tabs" ref={reviewTabsRef}>
            <div 
              className={`prof-review-pill-background ${isReviewPillReady ? 'animate' : ''}`} 
              style={{ 
                transform: `translateX(${reviewPillStyle.left}px)`, 
                width: `${reviewPillStyle.width}px`, 
                opacity: reviewPillStyle.opacity, 
                visibility: reviewPillStyle.opacity === 0 ? 'hidden' : 'visible' 
              }} 
            />
            <button className={`prof-review-tab ${reviewTab === 'rmp' ? 'active' : ''}`} onClick={() => setReviewTab('rmp')}>
              <span className="prof-review-tab-full">RateMyProfessor ({filteredRmpReviews.length})</span>
              <span className="prof-review-tab-short">RMP ({filteredRmpReviews.length})</span>
            </button>
            <button className={`prof-review-tab ${reviewTab === 'reddit' ? 'active' : ''}`} onClick={() => setReviewTab('reddit')}>
              <span className="prof-review-tab-full">Reddit ({redditMentions.length})</span>
              <span className="prof-review-tab-short">Reddit ({redditMentions.length})</span>
            </button>
          </div>
        </div>

        {reviewsLoading && (
          <div className="prof-loading" style={{ padding: '2rem 0' }}>
            <div className="prof-loading-spinner" />
          </div>
        )}

        {!reviewsLoading && reviewTab === 'rmp' && (
          <>
            <div className="prof-reviews-filters">
              <Dropdown className="feedback-dropdown" options={sortOptions} value={sortBy} onChange={setSortBy} placeholder="Sort by…" />
            </div>
            <div className="prof-reviews-list">
              {sortedReviews.length === 0 ? (
                <p className="prof-no-reviews">No reviews match current filters.</p>
              ) : (
                sortedReviews.slice(0, visibleReviews).map((r, i) => (
                  <div key={i} className={`prof-review-card ${isPinned(r.comment || '', pinSnippets.rmp) ? 'is-ask-pinned' : ''}`} style={{ borderLeftColor: r.quality >= 4 ? '#27ae60' : r.quality >= 3 ? '#f39c12' : '#e74c3c' }}>
                    {isPinned(r.comment || '', pinSnippets.rmp) && <span className="ask-pinned-label">From your question</span>}
                    <div className="prof-review-top">
                      <div className="prof-review-ratings">
                        <div className="prof-review-rating-item">
                          <span className="prof-review-rating-label">Quality</span>
                          <span className="prof-review-rating-value" data-score={String(r.quality)}>{r.quality}</span>
                        </div>
                        <div className="prof-review-rating-item">
                          <span className="prof-review-rating-label">Difficulty</span>
                          <span className="prof-review-rating-value" data-score={String(6 - r.difficulty)}>{r.difficulty}</span>
                        </div>
                      </div>
                      <div className="prof-review-meta">
                        <span className="prof-review-course">{getFormattedCourseCode(r.course)}</span>
                        <span className="prof-review-date">{formatReviewDate(r.date)}</span>
                      </div>
                    </div>
                    {r.comment && <p className="prof-review-comment">{r.comment}</p>}
                    <div className="prof-review-bottom">
                      {r.tags && (
                        <div className="prof-review-tags">
                          {r.tags.split('--').map(t => t.trim()).filter(Boolean).map((t, ti) => (
                            <span key={ti} className="prof-review-tag">{t}</span>
                          ))}
                        </div>
                      )}
                      <div className="prof-review-pills">
                        {r.grade && r.grade !== 'N/A' && <span className="prof-review-pill">Grade: {r.grade}</span>}
                        {r.attendance && r.attendance !== 'N/A' && <span className="prof-review-pill">Attendance: {r.attendance === 'true' || r.attendance === 'Mandatory' ? 'Mandatory' : 'Not Mandatory'}</span>}
                        {r.textbook && r.textbook !== 'N/A' && <span className="prof-review-pill">Textbook: {r.textbook === 'true' || r.textbook === 'Yes' ? 'Yes' : 'No'}</span>}
                        {r.online_class && r.online_class !== 'N/A' && <span className="prof-review-pill">{r.online_class === 'true' || r.online_class === 'Yes' ? 'Online' : 'In-Person'}</span>}
                      </div>
                    </div>
                  </div>
                ))
              )}
            </div>
            {visibleReviews < sortedReviews.length && (
              <button className="prof-load-more" onClick={() => setVisibleReviews(v => v + 10)}>
                Load More
              </button>
            )}
          </>
        )}

        {!reviewsLoading && reviewTab === 'reddit' && (
          <>
            <div className="prof-search-controls">
              <div className="prof-search-container">
                <input
                  type="text"
                  className="prof-search-input"
                  placeholder="Search Reddit mentions..."
                  value={redditSearch}
                  onChange={e => { setRedditSearch(e.target.value); setVisibleRedditMentions(10); }}
                />
              </div>
              <Dropdown className="prof-sort-dropdown" options={redditSentimentOptions} value={redditSentiment} onChange={(v) => { setRedditSentiment(v); setVisibleRedditMentions(10); }} />
            </div>
            <div className="prof-mention-list">
              {filteredRedditMentions.length === 0 ? (
                <p className="prof-no-reviews">No Reddit mentions found for this professor.</p>
              ) : (
                filteredRedditMentions.slice(0, visibleRedditMentions).map((m, i) => {
                  const redditUrl = safeRedditUrl(m.permalink);
                  const dateStr = formatRedditDate(m.created_utc);
                  const sScore = m.sentiment_score ?? 0;
                  // Clamp the pointer so the label/arrow never clip the card edge at extreme scores.
                  const markerPct = Math.min(90, Math.max(10, (sScore + 1) / 2 * 100));
                  const magnitudePct = Math.round(Math.abs(sScore) * 100);
                  const word = m.sentiment ? m.sentiment.charAt(0).toUpperCase() + m.sentiment.slice(1) : 'Neutral';
                  const label = `${word} (${magnitudePct}%)`;
                  return (
                  <div key={i} className={`reddit-comment-bubble ${isPinned(m.body || '', pinSnippets.reddit) ? 'is-ask-pinned' : ''}`}>
                    {isPinned(m.body || '', pinSnippets.reddit) && <span className="ask-pinned-label">From your question</span>}
                    <div className="reddit-comment-meta">
                      {dateStr && <span className="reddit-comment-date">{dateStr}</span>}
                      <div className="reddit-comment-actions">
                        {m.score != null && <span className="reddit-comment-score">▲ {m.score}</span>}
                        {redditUrl && <a className="reddit-comment-link" href={redditUrl} target="_blank" rel="noopener noreferrer">View on Reddit ↗</a>}
                      </div>
                    </div>
                    <p className="reddit-comment-body">{m.body}</p>
                    <div className="reddit-sentiment">
                      <div className="reddit-sentiment-pointer" style={{ left: `${markerPct}%` }}>
                        <span className="reddit-sentiment-label" style={{ color: sentimentBorderColor(m.sentiment) }}>{label}</span>
                        <span className="reddit-sentiment-arrow" style={{ color: sentimentBorderColor(m.sentiment) }}>▼</span>
                      </div>
                      <div className="reddit-sentiment-track" />
                    </div>
                  </div>
                  );
                })
              )}
            </div>
            {visibleRedditMentions < filteredRedditMentions.length && (
              <button className="prof-load-more" onClick={() => setVisibleRedditMentions(v => v + 10)}>
                Load More
              </button>
            )}
          </>
        )}
      </section>

      <Footer />
      <button
        className={`prof-back-to-top ${showBackToTop ? 'visible' : ''}`} 
        onClick={() => window.scrollTo({ top: 0, behavior: 'smooth' })} 
        aria-label="Back to top"
      >
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
          <polyline points="18 15 12 9 6 15" />
        </svg>
      </button>
    </div>
  );
};

export default Professor;