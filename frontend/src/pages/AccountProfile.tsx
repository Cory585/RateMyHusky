import { useAuth } from '../context/AuthContext';

export default function AccountProfile() {
  const { user, logout } = useAuth();
  if (!user) return null;

  return (
    <div className="account-profile">
      <div className="account-profile-header">
        {user.picture ? (
          <img src={user.picture} alt="" className="account-profile-avatar" referrerPolicy="no-referrer" />
        ) : (
          <div className="account-profile-avatar account-profile-avatar-fallback">
            {user.name.split(' ').map(n => n[0]).join('')}
          </div>
        )}
        <div>
          <h2 className="account-profile-name">{user.name}</h2>
          <p className="account-profile-email">{user.email}</p>
        </div>
      </div>
      <button className="account-signout-btn" onClick={() => logout()}>Sign Out</button>
    </div>
  );
}
