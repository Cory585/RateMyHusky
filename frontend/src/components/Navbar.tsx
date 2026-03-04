/**
 * Navbar.jsx
 * This component renders the navigation bar
 * It includes the logo, name of the application, a link to a list of all professors, and a sign-in button.
 */


import './Navbar.css';

const Navbar = () => {
  return (
    <nav className="navbar">
      <a href="/" className="navbar-logo">
        <span>Rate</span>MyHusky
      </a>

      <div className="navbar-right">
        <button className="signin-btn">Sign In</button>
      </div>
    </nav>
  );
};

export default Navbar;