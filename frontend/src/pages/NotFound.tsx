import { Link } from 'react-router-dom';
import Navbar from '../components/Navbar';
import Footer from '../components/Footer';
import ThemeToggle from '../components/ThemeToggle';
import './NotFound.css';

const NotFound = () => {
  return (
    <div className="not-found-page" style={{ display: 'flex', flexDirection: 'column', minHeight: '100vh' }}>
      <Navbar />
      <div className="not-found-content" style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', textAlign: 'center', padding: '2rem' }}>
        <h1 style={{ fontSize: '6rem', margin: 0, color: 'var(--primary-color)' }}>404</h1>
        <h2 style={{ fontSize: '2rem', marginBottom: '1rem' }}>Page Not Found</h2>
        <p style={{ marginBottom: '2rem', color: 'var(--text-secondary)' }}>
          The page you are looking for doesn't exist or has been moved.
        </p>
        <Link to="/" style={{ padding: '0.75rem 1.5rem', backgroundColor: 'var(--primary-color)', color: 'white', textDecoration: 'none', borderRadius: '4px', fontWeight: 'bold' }}>
          Back to Home
        </Link>
      </div>
      <Footer />
      <ThemeToggle />
    </div>
  );
};

export default NotFound;
