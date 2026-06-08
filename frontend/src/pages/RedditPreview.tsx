import './Professor.css';

interface RedditPost {
  id: string;
  subreddit: string;
  title: string;
  body: string;
  score: number;
  date: string;
  url: string;
  course?: string;
}

const MOCK_REDDIT_POSTS: RedditPost[] = [
  {
    id: 'r1',
    subreddit: 'r/NEU',
    title: 'Thoughts on taking CS3500 next semester?',
    body: "Just finished the class — honestly one of the best I've taken at Northeastern. The professor is super clear in lectures and very responsive to emails. Projects are tough but you genuinely learn a ton. Highly recommend if you can handle the workload.",
    score: 247,
    date: '2025-01-12',
    url: '#',
    course: 'CS 3500',
  },
  {
    id: 'r2',
    subreddit: 'r/NEU',
    title: 'Struggling with the midterm format',
    body: "Anyone else find the exams really tricky? Lectures are good but the midterm felt like it tested material we barely touched in class. That said the professor is really helpful during office hours — definitely go if you're lost.",
    score: 89,
    date: '2024-11-03',
    url: '#',
    course: 'DS 3000',
  },
  {
    id: 'r3',
    subreddit: 'r/Northeastern',
    title: 'Which section should I take next spring?',
    body: "Took this professor last spring and would 100% take again. Very organized, posts slides before class, and grading is fair. Co-op friendly too — they're understanding about scheduling conflicts.",
    score: 134,
    date: '2024-09-20',
    url: '#',
  },
];

export default function RedditPreview() {
  return (
    <div style={{ maxWidth: 800, margin: '40px auto', padding: '0 24px 80px' }}>
      <div style={{ marginBottom: 24, padding: '10px 16px', background: '#fffbea', border: '1px solid #f0d060', borderRadius: 10, fontSize: '0.85rem', color: '#7a6000' }}>
        Preview mode — this page is only for reviewing the Reddit UI and is not visible in production.
      </div>

      <section className="prof-section prof-reviews-section">
        <div className="prof-reviews-header">
          <h2 className="prof-section-title">Reviews</h2>
          <div className="prof-review-tabs">
            <div className="prof-review-pill-background animate" style={{ transform: 'translateX(246px)', width: 96, opacity: 1 }} />
            <button className="prof-review-tab">RateMyProfessor (12)</button>
            <button className="prof-review-tab">TRACE (38)</button>
            <button className="prof-review-tab prof-review-tab-reddit active">Reddit (3)</button>
          </div>
        </div>

        <div className="prof-reddit-container">
          <div className="prof-reddit-notice">
            <svg className="prof-reddit-notice-icon" viewBox="0 0 20 20" fill="currentColor">
              <path fillRule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7-4a1 1 0 11-2 0 1 1 0 012 0zM9 9a1 1 0 000 2v3a1 1 0 001 1h1a1 1 0 100-2v-3a1 1 0 00-1-1H9z" clipRule="evenodd" />
            </svg>
            Posts sourced live from Reddit. Content reflects student opinions and is not verified.
          </div>
          <div className="prof-reviews-list">
            {MOCK_REDDIT_POSTS.map(post => (
              <div key={post.id} className="prof-review-card prof-reddit-card">
                <div className="prof-review-top">
                  <div className="prof-reddit-score">
                    <svg className="prof-reddit-upvote" viewBox="0 0 24 24" fill="currentColor">
                      <path d="M12 4l8 8H4z" />
                    </svg>
                    <span className="prof-reddit-score-num">{post.score}</span>
                  </div>
                  <div className="prof-review-meta">
                    <span className="prof-reddit-sub-badge">{post.subreddit}</span>
                    <span className="prof-review-date">
                      {new Date(post.date).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })}
                    </span>
                  </div>
                </div>
                <p className="prof-reddit-title">{post.title}</p>
                <p className="prof-review-comment">{post.body}</p>
                <div className="prof-review-bottom">
                  <div className="prof-review-tags">
                    {post.course && <span className="prof-review-course">{post.course}</span>}
                  </div>
                  <a className="prof-reddit-link" href={post.url} target="_blank" rel="noopener noreferrer">
                    View on Reddit
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M18 13v6a2 2 0 01-2 2H5a2 2 0 01-2-2V8a2 2 0 012-2h6M15 3h6v6M10 14L21 3" />
                    </svg>
                  </a>
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>
    </div>
  );
}
