import Footer from '../components/Footer';
import './Terms.css';

const Privacy = () => {
  return (
    <div className="terms-page">
      <main className="terms-main">
        <div className="terms-shell">
          <header className="terms-header">
            <h1>Privacy Policy</h1>
            <p className="terms-meta">Effective June 29, 2026 &middot; RateMyHusky</p>
          </header>

          <div className="terms-body">
            <section className="terms-section">
              <h2>1. Introduction</h2>
              <p>
                RateMyHusky is an aggregator of professor and course information for
                Northeastern University students. This Privacy Policy describes what information
                we collect, how we use it, and the choices you have. By using RateMyHusky,
                you also agree to our{' '}
                <a href="/terms">Terms &amp; Conditions</a>.
              </p>
              <p>
                RateMyHusky is an independent student project and is not affiliated with,
                endorsed by, or officially connected to Northeastern University or RateMyProfessors.
              </p>
            </section>

            <section className="terms-section">
              <h2>2. Information We Collect</h2>
              <p>We collect only the minimum information needed to operate the service:</p>
              <ul>
                <li>
                  <strong>Google Sign-In:</strong> when you authenticate with your{' '}
                  <code>@husky.neu.edu</code> Google account, we receive your name, email
                  address, and profile photo from Google. This information is encoded in a
                  JWT token stored in your browser; it is never written to a server-side
                  database.
                </li>
                <li>
                  <strong>Browser preferences:</strong> your selected theme (dark/light) and
                  catalog view mode are saved to <code>localStorage</code> on your device only
                  and are never transmitted to our servers.
                </li>
                <li>
                  <strong>Feedback form:</strong> the feedback form collects a message type,
                  description, and an optional email address. Submissions are transmitted to
                  the RateMyHusky team via email and are not stored in a database. Submitted
                  information is used solely to improve the service. The form is protected by
                  a CAPTCHA challenge (see Third-Party Services). If you submit an "Ask Ban
                  Appeal" or a "Data Deletion Request," an email address is required so we can
                  respond, and (if you are signed in) your account identifier is included so we
                  can locate your Ask activity to review it or delete it. That account identifier
                  is derived from your sign-in token at the time you submit and is not retained
                  beyond handling your request.
                </li>
                <li>
                  <strong>Ask (AI question) feature:</strong> when you are signed in and submit
                  a question to the Ask feature, we log the question to operate the feature and
                  to detect abuse. Each logged entry includes the question text, the AI-generated
                  answer, the professor or course identified, how many tokens were used, the
                  response time, an identifier derived from your session token, and a one-way
                  hash of your IP address (your raw IP address is never stored). These logs are
                  retained on our servers — see <em>How We Store Your Information</em> below.
                </li>
              </ul>
              <p>
                Outside of the Ask feature, we do <strong>not</strong> log your search queries,
                which professor or course pages you viewed, or any other browsing activity on
                our servers.
              </p>
            </section>

            <section className="terms-section">
              <h2>3. How We Use Your Information</h2>
              <p>The information we collect is used solely to:</p>
              <ul>
                <li>Authenticate your identity and confirm your <code>@husky.neu.edu</code> affiliation</li>
                <li>Restrict access to TRACE course evaluation comments to signed-in users</li>
                <li>Display your name and profile photo in the navigation bar while signed in</li>
                <li>
                  Answer your Ask questions, enforce per-account and per-IP rate limits, and
                  detect and prevent abuse of the Ask feature (such as off-topic or
                  prompt-injection attempts)
                </li>
              </ul>
              <p>
                We do not use your information for advertising, profiling, or any purpose beyond
                operating the service. We do not sell, rent, or share your personal information
                with third parties for their own use.
              </p>
            </section>

            <section className="terms-section">
              <h2>4. How We Store Your Information</h2>
              <p>
                Your sign-in information is encoded in a JWT (JSON Web Token). During the
                Google OAuth handshake, a short-lived <code>httpOnly</code> cookie is used
                to facilitate the flow; once complete, the resulting JWT is stored in your
                browser's <code>localStorage</code> and the handshake cookie is cleared.
                The token expires automatically after 7 days. RateMyHusky does not maintain
                a persistent server-side user database; no account record is stored beyond
                the duration of your session token.
              </p>
              <p>
                Signing out deletes the token from your browser immediately.
              </p>
              <p>
                Separately, if you use the Ask feature, the question logs described above are
                stored server-side in our database. These logs are keyed to a session-derived
                identifier and a hashed IP address (never your raw IP) and are retained to
                operate the feature and detect abuse. You may request deletion of your Ask logs
                by submitting a <strong>Data Deletion Request</strong> through the feedback form
                while signed in — see <em>Your Rights &amp; Choices</em>.
              </p>
            </section>

            <section className="terms-section">
              <h2>5. Third-Party Services</h2>
              <p>RateMyHusky integrates with the following third-party services that may
                collect data under their own privacy policies:</p>
              <ul>
                <li>
                  <strong>Google OAuth 2.0</strong>: handles authentication. Your use of
                  Google sign-in is governed by{' '}
                  <a
                    href="https://policies.google.com/privacy"
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    Google's Privacy Policy
                  </a>.
                </li>
                <li>
                  <strong>Vercel Analytics &amp; Speed Insights</strong>: collects anonymous
                  page view and performance metrics. No personally identifiable information is
                  included. Subject to{' '}
                  <a
                    href="https://vercel.com/legal/privacy-policy"
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    Vercel's Privacy Policy
                  </a>.
                </li>
                <li>
                  <strong>Google Analytics</strong>: collects anonymous usage data such as page
                  views, engagement, and general geographic region to help us understand
                  how the service is used and improve it. Google Analytics may use cookies to
                  distinguish unique users. No personally identifiable information is shared
                  with Google Analytics. Subject to{' '}
                  <a
                    href="https://policies.google.com/privacy"
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    Google's Privacy Policy
                  </a>.
                </li>
                <li>
                  <strong>Groq</strong>: powers the Ask feature. When you submit an Ask question,
                  your question text and the relevant professor, course, and Reddit information
                  we retrieve are sent to Groq to classify the question and generate an answer.
                  We do not send Groq your name, email, or profile photo. Subject to{' '}
                  <a
                    href="https://groq.com/privacy-policy/"
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    Groq's Privacy Policy
                  </a>.
                </li>
                <li>
                  <strong>Resend</strong>: delivers email for the feedback form. When you submit
                  feedback, your message and any optional reply email address are transmitted
                  through Resend to reach our team. Subject to{' '}
                  <a
                    href="https://resend.com/legal/privacy-policy"
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    Resend's Privacy Policy
                  </a>.
                </li>
                <li>
                  <strong>Cloudflare Turnstile</strong>: a CAPTCHA that protects the feedback
                  form from automated abuse. It may process your IP address and browser signals
                  to verify you are human. Subject to{' '}
                  <a
                    href="https://www.cloudflare.com/privacypolicy/"
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    Cloudflare's Privacy Policy
                  </a>.
                </li>
                <li>
                  <strong>RateMyProfessors, Northeastern TRACE &amp; Reddit</strong>: these are
                  data sources only. We do not send any user data to these services.
                </li>
              </ul>
              <p>
                Our hosting provider (Vercel) may log standard server-side request data such
                as IP addresses and user-agent strings as part of normal infrastructure
                operation. This is subject to{' '}
                <a
                  href="https://vercel.com/legal/privacy-policy"
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  Vercel's Privacy Policy
                </a>.
              </p>
            </section>

            <section className="terms-section">
              <h2>6. Cookies &amp; Local Storage</h2>
              <p>
                During the Google OAuth sign-in flow, a short-lived <code>httpOnly</code> cookie
                is set to facilitate the authentication handshake; it is not used for tracking
                and is cleared after sign-in completes. Google Analytics may set cookies (e.g.,{' '}
                <code>_ga</code>, <code>_gid</code>) to distinguish unique users and track
                anonymous session data. You can opt out of Google Analytics tracking by
                installing the{' '}
                <a
                  href="https://tools.google.com/dlpage/gaoptout"
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  Google Analytics Opt-out Browser Add-on
                </a>.
              </p>
              <p>
                <code>localStorage</code> is used to store your JWT session token and browser
                preferences (theme, view mode). This data stays on your device and is never
                synced to our servers.
              </p>
            </section>

            <section className="terms-section">
              <h2>7. Your Rights &amp; Choices</h2>
              <p>
                Because we keep so little data, most of your data is under your direct control.
                The rights below describe what you can request and how to exercise them:
              </p>
              <ul>
                <li>
                  <strong>Right to access:</strong> you may request a copy of the data we hold
                  that is associated with you. In practice this is limited to your Ask feature
                  logs (if any); your sign-in details and preferences live only in your own
                  browser and are not accessible to us.
                </li>
                <li>
                  <strong>Right to deletion:</strong> you can <strong>sign out</strong> at any
                  time to immediately delete your JWT token from your browser, and{' '}
                  <strong>clear localStorage</strong> in your browser settings to remove your
                  session token and stored preferences. To delete the Ask feature logs we hold,
                  sign in and submit a <strong>Data Deletion Request</strong> through the feedback
                  form: an email address is required, and your signed-in account identifier is
                  included so we can verify your identity and delete every Ask log tied to your
                  account. (You may also email{' '}
                  <a href="mailto:support@ratemyhusky.com">support@ratemyhusky.com</a>, though
                  because we never store your email address, we can only act on a request we can
                  tie to your account — submitting the form while signed in is the reliable path.)
                  This is a separate request from appealing an Ask suspension: an{' '}
                  <strong>Ask Ban Appeal</strong> asks us to review and restore your access,
                  whereas a <strong>Data Deletion Request</strong> removes your Ask data from our
                  servers entirely.
                </li>
                <li>
                  <strong>Right to correction:</strong> the only personal data we receive (your
                  name, email, and photo) comes directly from Google and is never stored on our
                  servers, so corrections are made through your Google account. If you believe
                  professor or course data displayed on the site is inaccurate, you can report
                  it through the feedback form.
                </li>
                <li>
                  <strong>Right to raise a concern:</strong> you may contact us at any time at{' '}
                  <a href="mailto:support@ratemyhusky.com">support@ratemyhusky.com</a> with any
                  question or complaint about how your data is handled.
                </li>
              </ul>
              <p>
                Note that there is no account to delete. Once your token is cleared, no personal
                data remains in our systems, except for any Ask feature logs, which remain until
                you request their deletion.
              </p>
            </section>

            <section className="terms-section">
              <h2>8. Children's Privacy</h2>
              <p>
                RateMyHusky is intended for Northeastern University students (aged 18 and
                older) and is not directed at children. We do not knowingly collect personal
                information from anyone under the age of 18. If you believe a minor has
                provided us with personal information, please contact us at{' '}
                <a href="mailto:support@ratemyhusky.com">support@ratemyhusky.com</a>.
              </p>
            </section>

            <section className="terms-section">
              <h2>9. Changes to This Policy</h2>
              <p>
                We may update this Privacy Policy from time to time. The effective date at the
                top of this page will be updated when changes are made. Continued use of
                RateMyHusky after changes are posted constitutes your acceptance of the
                revised Policy.
              </p>
            </section>

            <section className="terms-section terms-section--last">
              <h2>10. Contact</h2>
              <p>
                If you have questions about this Privacy Policy or want to report a concern,
                please email us at{' '}
                <a href="mailto:support@ratemyhusky.com">support@ratemyhusky.com</a> or use
                the feedback form available at the bottom-right of any page on RateMyHusky.
              </p>
            </section>
          </div>
        </div>
      </main>
      <Footer />
    </div>
  );
};

export default Privacy;
