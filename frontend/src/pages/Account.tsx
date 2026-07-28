import { useLayoutEffect, useRef, useState } from 'react';
import { NavLink, Outlet, useLocation } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import SignInModal from '../components/SignInModal';
import './Account.css';

export default function Account() {
  const { user, loading } = useAuth();
  const [showSignIn, setShowSignIn] = useState(false);
  const location = useLocation();
  const profileTabRef = useRef<HTMLAnchorElement>(null);
  const bookmarksTabRef = useRef<HTMLAnchorElement>(null);
  const [indicator, setIndicator] = useState({ left: 0, width: 0 });

  useLayoutEffect(() => {
    const activeTab = location.pathname === '/account/bookmarks' ? bookmarksTabRef.current : profileTabRef.current;
    if (activeTab) {
      setIndicator({ left: activeTab.offsetLeft, width: activeTab.offsetWidth });
    }
  }, [location.pathname]);

  if (loading) return null;

  if (!user) {
    return (
      <div className="account-page">
        <div className="account-signin-gate">
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="account-lock-icon">
            <rect x="3" y="11" width="18" height="11" rx="2" ry="2" />
            <path d="M7 11V7a5 5 0 0 1 10 0v4" />
          </svg>
          <p>Sign in with your <span className="husky-email">husky.neu.edu</span> account to view your account and bookmarks.</p>
          <button className="account-signin-btn" onClick={() => setShowSignIn(true)}>Sign In</button>
        </div>
        <SignInModal open={showSignIn} onClose={() => setShowSignIn(false)} />
      </div>
    );
  }

  return (
    <div className="account-page">
      <div className="account-shell">
        <nav className="account-tabs">
          <NavLink ref={profileTabRef} to="/account" end className={({ isActive }) => `account-tab${isActive ? ' active' : ''}`}>
            Profile
          </NavLink>
          <NavLink ref={bookmarksTabRef} to="/account/bookmarks" className={({ isActive }) => `account-tab${isActive ? ' active' : ''}`}>
            Bookmarks
          </NavLink>
          <span className="account-tab-indicator" style={{ transform: `translateX(${indicator.left}px)`, width: indicator.width }} />
        </nav>
        <div className="account-tab-content">
          <Outlet />
        </div>
      </div>
    </div>
  );
}
