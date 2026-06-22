import pytest


@pytest.fixture
def render_client(monkeypatch):
    """A Flask test client for the render blueprint with the server's data
    view functions stubbed, so no DB is needed."""
    import render

    # Stubs returning Flask-like JSON via a tiny fake response object.
    class FakeResp:
        def __init__(self, data, status=200):
            self._data = data
            self.status_code = status
        def get_json(self):
            return self._data

    def fake_professor_profile(slug):
        if slug == "missing":
            return ({"error": "not found"}, 404)
        return FakeResp({
            "name": "Francis Georges", "department": "Economics",
            "avgRating": 4.25, "totalRatings": 2686, "wouldTakeAgainPct": 83,
            "difficulty": 2.9, "rmpRating": 4.3, "traceRating": 4.2,
            "imageUrl": None, "professorUrl": None, "traceCourses": [],
        })

    def fake_professor_reviews(slug):
        return FakeResp({"reviews": [
            {"course": "ECON1115", "quality": 5, "difficulty": 3,
             "date": "2024", "comment": "Excellent lecturer."}
        ], "traceComments": []})

    def fake_course_detail(code):
        if code == "missing":
            return ({"error": "not found"}, 404)
        return FakeResp({
            "summary": {"code": "ECON1115", "name": "Macroeconomics",
                        "department": "Economics", "avgRating": 4.1,
                        "avgEnrollment": 120, "latestTermTitle": "Fall 2025"},
            "instructors": [{"name": "Francis Georges", "slug": "francis-georges"}],
            "sections": [], "questionScores": [],
        })

    monkeypatch.setattr(render, "_get_profile_view", lambda: fake_professor_profile, raising=False)
    monkeypatch.setattr(render, "_get_reviews_view", lambda: fake_professor_reviews, raising=False)
    monkeypatch.setattr(render, "_get_course_view", lambda: fake_course_detail, raising=False)

    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(render.render_bp)
    return app.test_client()
