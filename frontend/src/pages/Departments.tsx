import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { fetchDepartmentsHub, type HubDepartment } from '../api/api';
import Footer from '../components/Footer';
import Seo from '../components/Seo';
import './ProfessorCatalog.css';
import './Departments.css';

export default function Departments() {
  const [departments, setDepartments] = useState<HubDepartment[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchDepartmentsHub()
      .then(data => {
        setDepartments(data.departments);
        setTotal(data.total);
      })
      .catch(console.error)
      .finally(() => setLoading(false));
  }, []);

  const jsonLd = {
    '@context': 'https://schema.org',
    '@type': 'ItemList',
    itemListElement: departments.map((d, i) => ({
      '@type': 'ListItem',
      position: i + 1,
      name: d.name,
      url: `https://ratemyhusky.com/departments/${d.slug}`,
    })),
  };

  return (
    <div className="catalog-page">
      <Seo
        title="Northeastern Departments — Professor Ratings & Reviews | RateMyHusky"
        description={`Browse ${total ? total.toLocaleString() : 'dozens of'} Northeastern University (NEU) academic departments. Compare professor ratings, difficulty, and would-take-again by department.`}
        canonical="https://ratemyhusky.com/departments"
        jsonLd={jsonLd}
      />

      <div className="catalog-header">
        <h1 className="catalog-title">Northeastern University Departments — Professor Ratings by Department</h1>
        <span className="catalog-count">
          {loading ? '…' : `${total.toLocaleString()} department${total !== 1 ? 's' : ''}`}
        </span>
      </div>

      <main className="catalog-main">
        {loading ? (
          <div className="catalog-list">
            {Array.from({ length: 10 }).map((_, i) => (
              <div key={i} className="prof-list-item skeleton" />
            ))}
          </div>
        ) : (
          <div className="catalog-list">
            {departments.map(dept => (
              <Link key={dept.slug} to={`/departments/${dept.slug}`} className="dept-hub-item">
                <div className="dept-hub-info">
                  <span className="dept-hub-name">{dept.name}</span>
                  <span className="dept-hub-count">{dept.professorCount.toLocaleString()} professors</span>
                </div>
                <span className="dept-hub-rating">
                  {dept.avgRating != null ? dept.avgRating.toFixed(2) : 'N/A'}
                </span>
              </Link>
            ))}
          </div>
        )}
      </main>
      <Footer />
    </div>
  );
}
