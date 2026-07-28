import { useState } from 'react';
import { NavLink, Outlet } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import SignInModal from '../components/SignInModal';
import './Account.css';

export default function Account() {
  const { user, loading } = useAuth();
  const [showSignIn, setShowSignIn] = useState(false);

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
          <NavLink to="/account" end className={({ isActive }) => `account-tab${isActive ? ' active' : ''}`}>
            Profile
          </NavLink>
          <NavLink to="/account/bookmarks" className={({ isActive }) => `account-tab${isActive ? ' active' : ''}`}>
            Bookmarks
          </NavLink>
        </nav>
        <div className="account-tab-content">
          <Outlet />
        </div>
      </div>
    </div>
  );
}
