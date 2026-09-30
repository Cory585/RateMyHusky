import { useEffect, useMemo, useRef, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import StarRating from '../components/StarRating';
import NotFound from './NotFound';
import { fetchCourseData } from '../api/api';
import type { CourseDetail } from '../api/api';
import Footer from '../components/Footer';
import { getInitials, stripPrefix } from '../utils/nameUtils';
import Breadcrumbs from '../components/Breadcrumbs';
import Seo from '../components/Seo';
import BookmarkButton from '../components/BookmarkButton';
import './Course.css';

const INITIAL_INSTRUCTORS_VISIBLE = 5;
const INSTRUCTORS_VISIBLE_STEP = 5;

const Course = () => {
	const { code = '' } = useParams<{ code: string }>();
	const [course, setCourse] = useState<CourseDetail | null>(null);
	const [loading, setLoading] = useState(true);
	const [notFound, setNotFound] = useState(false);
	const [visibleInstructorCount, setVisibleInstructorCount] = useState(INITIAL_INSTRUCTORS_VISIBLE);
	const [showBackToTop, setShowBackToTop] = useState(false);
	const tableWrapRef = useRef<HTMLDivElement>(null);
	const [tableAtStart, setTableAtStart] = useState(true);
	const [tableAtEnd, setTableAtEnd] = useState(false);

	useEffect(() => {
		let cancelled = false;

		const load = async () => {
			setLoading(true);
			setNotFound(false);
			const data = await fetchCourseData(code);
			if (cancelled) return;
			if (!data) {
				setCourse(null);
				setNotFound(true);
			} else {
				setCourse(data);
			}
			setLoading(false);
		};

		load();
		return () => {
			cancelled = true;
		};
	}, [code]);

	useEffect(() => {
		const handler = () => setShowBackToTop(window.scrollY > 300);
		window.addEventListener('scroll', handler, { passive: true });
		handler();
		return () => window.removeEventListener('scroll', handler);
	}, []);

	useEffect(() => {
		const el = tableWrapRef.current;
		if (!el) return;
		const check = () => {
			setTableAtStart(el.scrollLeft <= 10);
			setTableAtEnd(el.scrollLeft + el.clientWidth >= el.scrollWidth - 10);
		};
		check();
		el.addEventListener('scroll', check, { passive: true });
		window.addEventListener('resize', check);
		return () => {
			el.removeEventListener('scroll', check);
			window.removeEventListener('resize', check);
		};
	}, [course]);

	const recentInstructors = useMemo(() => {
		if (!course) return [];
		const currentYear = new Date().getFullYear();
		const yearOf = (d: string | null) => {
			const y = d ? new Date(d).getFullYear() : NaN;
			return Number.isNaN(y) ? null : y;
		};
		const timeOf = (d: string | null) => (d ? new Date(d).getTime() || 0 : 0);

		const getInstructorsWithinYears = (yearsBack: number) => {
			const cutoffYear = currentYear - yearsBack;
			return course.instructors.filter(inst => {
				const year = yearOf(inst.latestDate);
				return year !== null && year >= cutoffYear;
			});
		};

		// Start with 1 year, expand until at least 5 or no more data
		let result = getInstructorsWithinYears(1);
		let yearsBack = 1;
		const maxYear = currentYear - 2000 + 1; // won't go past year 2000
		while (result.length < 5 && yearsBack < maxYear) {
			yearsBack++;
			result = getInstructorsWithinYears(yearsBack);
		}

		// If no recency data available, fall back to all instructors sorted by rating
		if (!result.length) {
			return [...course.instructors]
				.sort((a, b) => (b.avgRating ?? -1) - (a.avgRating ?? -1))
				.slice(0, 10);
		}

		const sorted = result.sort((a, b) => {
			const aTime = timeOf(a.latestDate);
			const bTime = timeOf(b.latestDate);
			if (bTime !== aTime) return bTime - aTime;
			return (b.avgRating ?? -1) - (a.avgRating ?? -1);
		});

		if (sorted.length > 10) {
			return sorted
				.sort((a, b) => (b.avgRating ?? -1) - (a.avgRating ?? -1))
				.slice(0, 10);
		}
		return sorted;
	}, [course]);

	const avgDifficulty = useMemo(() => {
		if (!course) return null;
		const valid = course.instructors.filter(i => i.courseAvgDifficulty != null);
		if (!valid.length) return null;
		return valid.reduce((sum, i) => sum + i.courseAvgDifficulty!, 0) / valid.length;
	}, [course]);

	if (loading) {
		return (
			<div className="course-page">
				<div className="course-shell">
					<div className="course-loading">Loading course data...</div>
				</div>
			</div>
		);
	}

	if (notFound || !course) {
		return <NotFound />;
	}

	const summary = course.summary;
	const visibleInstructors = course.instructors.slice(0, visibleInstructorCount);
	const hasMoreInstructors = visibleInstructorCount < course.instructors.length;
	const canCollapseInstructors = visibleInstructorCount > INITIAL_INSTRUCTORS_VISIBLE;
	const hasExpandableInstructors = course.instructors.length > INITIAL_INSTRUCTORS_VISIBLE;

	const courseName = summary.name || summary.code;
	const lastReviewed = summary.latestDate ? new Date(summary.latestDate) : null;
	const lastReviewedLabel = lastReviewed && !Number.isNaN(lastReviewed.getTime())
		? lastReviewed.toLocaleDateString('en-US', { month: 'short', year: 'numeric' })
		: 'Unknown';

	const courseSeoDescription =
		`${summary.code}${summary.name ? ` (${summary.name})` : ''} course reviews and ratings at Northeastern (NEU). ` +
		(summary.avgRating != null ? `Average rating ${summary.avgRating.toFixed(1)}/5. ` : '') +
		`Compare instructors with RateMyProfessors reviews.`;

	const courseCanonical = `https://ratemyhusky.com/courses/${code}`;
	const courseJsonLd: Record<string, unknown> = {
		'@context': 'https://schema.org',
		'@type': 'Course',
		name: summary.name ? `${summary.code} — ${summary.name}` : summary.code,
		courseCode: summary.code,
		provider: { '@type': 'CollegeOrUniversity', name: 'Northeastern University' },
	};
	if (summary.avgRating != null && summary.numRatings) {
		courseJsonLd.aggregateRating = {
			'@type': 'AggregateRating',
			ratingValue: summary.avgRating,
			ratingCount: summary.numRatings,
			bestRating: 5,
		};
	}
	const courseBreadcrumbJsonLd = {
		'@context': 'https://schema.org',
		'@type': 'BreadcrumbList',
		itemListElement: [
			{ '@type': 'ListItem', position: 1, name: 'Home', item: 'https://ratemyhusky.com/' },
			{ '@type': 'ListItem', position: 2, name: 'Courses', item: 'https://ratemyhusky.com/courses' },
			{ '@type': 'ListItem', position: 3, name: summary.code, item: courseCanonical },
		],
	};

	return (
		<div className="course-page">
			<Seo
				title={summary.name ? `${summary.code} Reviews — ${summary.name} at Northeastern` : `${summary.code} Reviews at Northeastern`}
				description={courseSeoDescription}
				canonical={courseCanonical}
				jsonLd={[courseJsonLd, courseBreadcrumbJsonLd]}
			/>
			<div className="course-shell">
				<Breadcrumbs items={[
					{ label: 'Courses', to: '/courses' },
					{ label: summary.code },
				]} />

				<header className="course-hero">
					<div>
						<p className="course-code">{summary.code}</p>
						<h1>
							{courseName}
							<BookmarkButton itemType="course" itemKey={summary.code} size="md" className="course-hero-bookmark" />
						</h1>
						<p className="course-dept">{summary.department}</p>
					</div>
				</header>

				<section className="course-stats-grid">
					<RatingStatCard avgRating={summary.avgRating} />
					<DifficultyStatCard value={avgDifficulty} />
					<StatCard label="Ratings" value={summary.numRatings.toLocaleString()} />
					<StatCard label="Instructors" value={course.instructors.length.toLocaleString()} />
					<StatCard label="Last Reviewed" value={lastReviewedLabel} className="course-stat-last-reviewed" />
				</section>

				{recentInstructors.length > 0 && (
					<section className="course-panel">
						<div className="course-panel-header">
							<h2>Recent Professors</h2>
						</div>
						<div className="course-top-prof-grid">
							{recentInstructors.map((prof, index) => (
								<Link
									to={prof.slug ? `/professors/${prof.slug}` : '#'}
									state={prof.slug ? { fromPage: { label: summary.name ? `${code.toUpperCase()} – ${summary.name}` : code.toUpperCase(), url: `/courses/${code}` } } : undefined}
									className={`course-top-prof-card${prof.slug ? '' : ' disabled'}`}
									key={`${prof.name}-${index}`}
									aria-label={prof.slug ? `View ${prof.name}` : `${prof.name} profile unavailable`}
									onClick={(e) => {
										if (!prof.slug) e.preventDefault();
									}}
								>
									<div className="course-top-prof-photo">
										{prof.imageUrl ? (
											<img
												className="course-top-prof-img"
												src={prof.imageUrl}
												alt={prof.name}
											/>
										) : (
											<div className="course-top-prof-avatar" aria-hidden="true">
												{getInitials(prof.name)}
											</div>
										)}
										<span className="course-top-prof-rank">#{index + 1}</span>
									</div>
									<div className="course-top-prof-body">
										<div className="course-top-prof-body-top">
											<div className="course-top-prof-rating">
												{prof.avgRating != null ? (
													<>
														<span className="course-top-prof-avg">{prof.avgRating.toFixed(1)}</span>
														<StarRating rating={prof.avgRating} size="sm" />
													</>
												) : (
													<span className="course-top-prof-avg">—</span>
												)}
											</div>
											<h3 className="course-top-prof-name">
												{stripPrefix(prof.name)}
											</h3>
										</div>
										<div className="course-top-prof-footer">
											<span>{prof.totalReviews.toLocaleString()} ratings</span>
											<span className="course-top-prof-footer--center">{prof.totalComments.toLocaleString()} comments</span>
											<span className="course-top-prof-footer--right">{prof.wouldTakeAgainPct != null ? `${Math.round(prof.wouldTakeAgainPct)}% again` : '—'}</span>
										</div>
									</div>
								</Link>
							))}
						</div>
					</section>
				)}

				<section className="course-panel">
					<div className="course-panel-header">
						<h2>Instructor Breakdown</h2>
					</div>
					<div className={`instructor-scroll-wrap${!tableAtStart ? ' fade-left' : ''}${!tableAtEnd ? ' fade-right' : ''}`}>
						<div className="instructor-scroll-inner" ref={tableWrapRef}>
							<div className="instructor-header-row">
								<span>Professor Name</span>
								<span>Rating</span>
								<span>Difficulty</span>
							</div>
							{visibleInstructors.map((row) => (
								<div key={row.name} className="instructor-row">
									<span>{row.name}</span>
											<span>{row.avgRating != null ? row.avgRating.toFixed(2) : '—'}</span>
											<span>{row.courseAvgDifficulty != null ? row.courseAvgDifficulty.toFixed(2) : '—'}</span>
								</div>
							))}
						</div>
					</div>
					{hasExpandableInstructors && (
						<div className="course-expand-controls">
							<button
								type="button"
								className="course-expand-btn"
								disabled={!canCollapseInstructors}
								onClick={() => setVisibleInstructorCount(INITIAL_INSTRUCTORS_VISIBLE)}
							>
								<span className="course-expand-chevron up" />
							</button>
							<button
								type="button"
								className="course-expand-btn"
								disabled={!hasMoreInstructors}
								onClick={() => setVisibleInstructorCount((prev) => Math.min(prev + INSTRUCTORS_VISIBLE_STEP, course.instructors.length))}
							>
								<span className="course-expand-chevron down" />
							</button>
						</div>
					)}
				</section>

			</div>
			<button
			className={`back-to-top ${showBackToTop ? 'visible' : ''}`}
			onClick={() => window.scrollTo({ top: 0, behavior: 'smooth' })}
			aria-label="Back to top"
		>
			<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
				<polyline points="18 15 12 9 6 15" />
			</svg>
		</button>
		<Footer />
		</div>
	);
};

function RatingStatCard({ avgRating }: { avgRating: number | null }) {
	return (
		<article className="course-stat-card">
			<strong className="course-stat-value">{avgRating != null ? avgRating.toFixed(2) : '—'}</strong>
			<StarRating rating={avgRating ?? 0} size="lg" />
			<span className="course-stat-label">Overall Rating</span>
		</article>
	);
}

function StatCard({ label, value, className }: { label: string; value: string; className?: string }) {
	return (
		<article className={`course-stat-card${className ? ` ${className}` : ''}`}>
			<strong className="course-stat-value">{value}</strong>
			<span className="course-stat-label">{label}</span>
		</article>
	);
}

function DifficultyStatCard({ value }: { value: number | null }) {
	const color = value == null ? '#eee'
		: value <= 1.5 ? '#27ae60'
		: value <= 2.5 ? '#66bd63'
		: value <= 3.0 ? '#f39c12'
		: value <= 3.5 ? '#e67e22'
		: value <= 4.0 ? '#e74c3c'
		: '#c0392b';
	return (
		<article className="course-stat-card">
			<strong className="course-stat-value">{value != null ? value.toFixed(2) : '—'}</strong>
			<div className="course-difficulty-bar">
				<div className="course-difficulty-fill" style={{ width: `${((value ?? 0) / 5) * 100}%`, background: color }} />
			</div>
			<span className="course-stat-label">Avg Difficulty</span>
		</article>
	);
}

export default Course;
