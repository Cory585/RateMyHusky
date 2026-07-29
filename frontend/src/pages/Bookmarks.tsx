import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { useBookmarks } from '../context/BookmarksContext';
import type { BookmarkedProfessor, BookmarkedCourse } from '../context/BookmarksContext';
import BookmarkButton from '../components/BookmarkButton';
import StarRating from '../components/StarRating';
import SignInModal from '../components/SignInModal';
import Footer from '../components/Footer';
import { getInitials, splitProfName, stripPrefix } from '../utils/nameUtils';
import './ProfessorCatalog.css';
import './Courses.css';
import './Bookmarks.css';

function ratingColor(v: number | null): 'high' | 'mid' | 'low' | 'neutral' {
  if (v === null) return 'neutral';
  if (v >= 4) return 'high';
  if (v >= 3) return 'mid';
  return 'low';
}

const BOOKMARK_ICON = (
  <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z" />
  </svg>
);

// Exact copy of the catalog grid card (ProfessorCatalog.tsx:756-824), including
// the footer stats row the old bookmarks page dropped.
function ProfCard({ prof, onOpen }: { prof: BookmarkedProfessor; onOpen: () => void }) {
  return (
    <div
      className="prof-card"
      role="button"
      tabIndex={0}
      onClick={onOpen}
      onKeyDown={e => e.key === 'Enter' && e.target === e.currentTarget && onOpen()}
    >
      <BookmarkButton itemType="professor" itemKey={prof.slug} size="sm" className="prof-card-bookmark" />
      <div className="prof-card-photo">
        <div className="prof-avatar">
          {prof.imageUrl ? (
            <img
              src={prof.imageUrl}
              alt=""
              className="prof-avatar-img"
              style={{ objectPosition: `${prof.focusX ?? 50}% ${prof.focusY ?? 30}%` }}
              onError={(e) => {
                const target = e.currentTarget;
                target.style.display = 'none';
                const fallback = target.parentElement?.querySelector('.prof-avatar-initials') as HTMLElement;
                if (fallback) fallback.style.display = 'flex';
              }}
            />
          ) : null}
          <span className="prof-avatar-initials" style={prof.imageUrl ? { display: 'none' } : undefined}>
            {getInitials(prof.name)}
          </span>
        </div>
      </div>
      <div className="prof-card-info">
        <div className="prof-card-info-top">
          <h3 className="prof-name">
            {(() => {
              const [first, rest] = splitProfName(stripPrefix(prof.name));
              return rest ? <>{first}<br />{rest}</> : first;
            })()}
          </h3>
          <div className="prof-card-rating-row">
            <span className="prof-avg-num">{prof.avgRating != null ? prof.avgRating.toFixed(1) : 'N/A'}</span>
            <StarRating rating={prof.avgRating ?? 0} size="sm" />
          </div>
        </div>
        <span className="prof-college">{prof.college}</span>
        <span className="prof-dept-label">{prof.department}</span>
        <div className="prof-sub-ratings">
          <div className="sub-rating-item" data-color={ratingColor(prof.rmpRating)}>
            <span className="sub-rating-val">{prof.rmpRating != null ? prof.rmpRating.toFixed(1) : '—'}</span>
            <span className="sub-rating-lbl">RMP</span>
          </div>
          <div className="sub-rating-item" data-color={ratingColor(prof.traceRating)}>
            <span className="sub-rating-val">{prof.traceRating != null ? prof.traceRating.toFixed(1) : '—'}</span>
            <span className="sub-rating-lbl">TRACE</span>
          </div>
        </div>
        <div className="prof-card-footer">
          <span className="prof-rating-count">{prof.totalReviews.toLocaleString()} ratings</span>
          <span className="prof-rating-count prof-rating-count--center">{prof.totalComments.toLocaleString()} comments</span>
          <span className="prof-rating-count">{prof.wouldTakeAgainPct != null ? `${Math.round(prof.wouldTakeAgainPct)}% again` : '—'}</span>
        </div>
      </div>
    </div>
  );
}

// Exact copy of the course catalog card (Courses.tsx:478-505).
function CourseCard({ course, onOpen }: { course: BookmarkedCourse; onOpen: () => void }) {
  return (
    <div
      className="prof-card course-card"
      role="button"
      tabIndex={0}
      onClick={onOpen}
      onKeyDown={e => e.key === 'Enter' && e.target === e.currentTarget && onOpen()}
    >
      <BookmarkButton itemType="course" itemKey={course.code} size="sm" className="prof-card-bookmark" />
      <div className="course-card-header">
        <span className="course-card-code">{course.code}</span>
      </div>
      <div className="prof-body">
        <h3 className="prof-name">{course.name}</h3>
        <p className="prof-dept">{course.department}</p>
        <div className="prof-rating-row">
          {course.avgRating != null ? (
            <>
              <span className="prof-avg-num">{course.avgRating.toFixed(2)}</span>
              <StarRating rating={course.avgRating} size="sm" />
            </>
          ) : (
            <span className="prof-avg na">N/A</span>
          )}
        </div>
      </div>
    </div>
  );
}

export default function Bookmarks() {
  const navigate = useNavigate();
  const { user, loading: authLoading } = useAuth();
  const { bookmarkedProfessors, bookmarkedCourses, loading } = useBookmarks();
  const [showSignIn, setShowSignIn] = useState(false);

  if (authLoading) return null;

  if (!user) {
    return (
      <div className="catalog-page bookmarks-page">
        <div className="bm-signin-gate">
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="bm-lock-icon">
            <rect x="3" y="11" width="18" height="11" rx="2" ry="2" />
            <path d="M7 11V7a5 5 0 0 1 10 0v4" />
          </svg>
          <p>Sign in with your <span className="husky-email">husky.neu.edu</span> account to view your bookmarks.</p>
          <button className="bm-signin-btn" onClick={() => setShowSignIn(true)}>Sign In</button>
        </div>
        <SignInModal open={showSignIn} onClose={() => setShowSignIn(false)} />
      </div>
    );
  }

  if (loading) {
    return (
      <div className="catalog-page bookmarks-page">
        <div className="catalog-header">
          <h1 className="catalog-title">Bookmarks</h1>
          <span className="catalog-count">…</span>
        </div>
        <div className="bm-content">
          <div className="catalog-grid">
            {Array.from({ length: 8 }).map((_, i) => (
              <div key={i} className="prof-card skeleton" />
            ))}
          </div>
        </div>
      </div>
    );
  }

  const total = bookmarkedProfessors.length + bookmarkedCourses.length;

  return (
    <div className="catalog-page bookmarks-page">
      <div className="catalog-header">
        <h1 className="catalog-title">Bookmarks</h1>
        <span className="catalog-count">{total} saved</span>
      </div>

      {total === 0 ? (
        <div className="catalog-empty">
          <div className="bm-empty-icon">{BOOKMARK_ICON}</div>
          <p className="bm-empty-title">You haven't bookmarked anything yet</p>
          <p className="bm-empty-hint">Tap the bookmark flag on any professor or course card to save it here for quick access.</p>
          <div className="bm-empty-ctas">
            <Link to="/professors" className="clear-btn prominent">Browse Professors</Link>
            <Link to="/courses" className="clear-btn prominent secondary">Browse Courses</Link>
          </div>
        </div>
      ) : (
        <div className="bm-content">
          {bookmarkedProfessors.length > 0 && (
            <section>
              <div className="bm-section-head">
                <h2 className="bm-section-title">Professors</h2>
                <span className="bm-section-count">{bookmarkedProfessors.length}</span>
              </div>
              <div className="catalog-grid">
                {bookmarkedProfessors.map(prof => (
                  <ProfCard key={prof.slug} prof={prof} onOpen={() => navigate(`/professors/${prof.slug}`)} />
                ))}
              </div>
            </section>
          )}
          {bookmarkedCourses.length > 0 && (
            <section>
              <div className="bm-section-head">
                <h2 className="bm-section-title">Courses</h2>
                <span className="bm-section-count">{bookmarkedCourses.length}</span>
              </div>
              <div className="catalog-grid">
                {bookmarkedCourses.map(course => (
                  <CourseCard key={course.code} course={course} onOpen={() => navigate(`/courses/${course.code.toLowerCase()}`)} />
                ))}
              </div>
            </section>
          )}
        </div>
      )}
      <Footer />
    </div>
  );
}
