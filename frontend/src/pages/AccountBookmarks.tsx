import { useNavigate, Link } from 'react-router-dom';
import { useBookmarks } from '../context/BookmarksContext';
import BookmarkButton from '../components/BookmarkButton';
import StarRating from '../components/StarRating';
import { getInitials, splitProfName, stripPrefix } from '../utils/nameUtils';
import '../pages/ProfessorCatalog.css';

function ratingColor(v: number | null): 'high' | 'mid' | 'low' | 'neutral' {
  if (v === null) return 'neutral';
  if (v >= 4) return 'high';
  if (v >= 3) return 'mid';
  return 'low';
}

export default function AccountBookmarks() {
  const navigate = useNavigate();
  const { bookmarkedProfessors, bookmarkedCourses, loading } = useBookmarks();

  if (loading) return null;

  if (bookmarkedProfessors.length === 0 && bookmarkedCourses.length === 0) {
    return (
      <div className="catalog-empty">
        <p>You haven't bookmarked any professors or courses yet.</p>
        <div style={{ display: 'flex', gap: 12 }}>
          <Link to="/professors" className="clear-btn prominent">Browse Professors</Link>
          <Link to="/courses" className="clear-btn prominent">Browse Courses</Link>
        </div>
      </div>
    );
  }

  return (
    <div className="account-bookmarks">
      {bookmarkedProfessors.length > 0 && (
        <section>
          <h3 className="account-bookmarks-heading">Professors</h3>
          <div className="catalog-grid">
            {bookmarkedProfessors.map(prof => (
              <div
                key={prof.slug}
                className="prof-card"
                role="button"
                tabIndex={0}
                onClick={() => navigate(`/professors/${prof.slug}`)}
                onKeyDown={e => e.key === 'Enter' && e.target === e.currentTarget && navigate(`/professors/${prof.slug}`)}
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
                    <span
                      className="prof-avatar-initials"
                      style={prof.imageUrl ? { display: 'none' } : undefined}
                    >
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
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      {bookmarkedCourses.length > 0 && (
        <section>
          <h3 className="account-bookmarks-heading">Courses</h3>
          <div className="catalog-grid">
            {bookmarkedCourses.map(course => (
              <div
                key={course.code}
                className="prof-card course-card"
                role="button"
                tabIndex={0}
                onClick={() => navigate(`/courses/${course.code.toLowerCase()}`)}
                onKeyDown={e => e.key === 'Enter' && e.target === e.currentTarget && navigate(`/courses/${course.code.toLowerCase()}`)}
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
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
