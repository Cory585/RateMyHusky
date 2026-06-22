import json
import re
from render import (
    professor_html, course_html, not_found_html, _esc, MAX_SNAPSHOT_REVIEWS,
)


def _extract_jsonld(html):
    blocks = re.findall(
        r'<script type="application/ld\+json">(.*?)</script>', html, re.DOTALL
    )
    return [json.loads(b) for b in blocks]


def test_esc_escapes_html_and_handles_none():
    assert _esc(None) == ""
    assert _esc("<b>&'\"") == "&lt;b&gt;&amp;&#x27;&quot;"
    assert _esc(4.25) == "4.25"


def test_professor_html_has_title_canonical_and_h1():
    profile = {
        "name": "Francis Georges", "department": "Economics",
        "avgRating": 4.25, "totalRatings": 2686, "wouldTakeAgainPct": 83,
        "difficulty": 2.9, "rmpRating": 4.3, "traceRating": 4.2,
        "imageUrl": "https://img/x.jpg", "professorUrl": None,
        "traceCourses": [{"displayName": "ECON1115: Macro"}],
    }
    html = professor_html(profile, [], "https://ratemyhusky.com/professors/francis-georges")
    assert "<!doctype html>" in html.lower()
    assert "<title>Francis Georges — Economics at Northeastern | RateMyHusky</title>" in html
    assert '<link rel="canonical" href="https://ratemyhusky.com/professors/francis-georges"' in html
    assert "<h1>Francis Georges — Economics at Northeastern University</h1>" in html
    # summary paragraph mentions the key numbers
    assert "4.25" in html and "2686" in html and "83%" in html


def test_professor_html_jsonld_person_with_aggregate_rating():
    profile = {
        "name": "Francis Georges", "department": "Economics",
        "avgRating": 4.25, "totalRatings": 2686, "wouldTakeAgainPct": 83,
        "difficulty": 2.9, "rmpRating": None, "traceRating": None,
        "imageUrl": None, "professorUrl": None, "traceCourses": [],
    }
    html = professor_html(profile, [], "https://ratemyhusky.com/professors/francis-georges")
    blocks = _extract_jsonld(html)
    assert len(blocks) == 1
    block = blocks[0]
    assert block["@type"] == "Person"
    assert block["name"] == "Francis Georges"
    assert block["aggregateRating"]["ratingValue"] == "4.25"
    assert block["aggregateRating"]["ratingCount"] == 2686


def test_professor_html_omits_aggregate_rating_when_no_ratings():
    profile = {
        "name": "New Prof", "department": "Music",
        "avgRating": 0.0, "totalRatings": 0, "wouldTakeAgainPct": None,
        "difficulty": None, "rmpRating": None, "traceRating": None,
        "imageUrl": None, "professorUrl": None, "traceCourses": [],
    }
    html = professor_html(profile, [], "https://ratemyhusky.com/professors/new-prof")
    block = _extract_jsonld(html)[0]
    assert "aggregateRating" not in block


def test_professor_html_escapes_review_comment():
    profile = {
        "name": "X Y", "department": "CS", "avgRating": 3.0, "totalRatings": 1,
        "wouldTakeAgainPct": None, "difficulty": None, "rmpRating": None,
        "traceRating": None, "imageUrl": None, "professorUrl": None, "traceCourses": [],
    }
    reviews = [{"course": "CS1", "quality": 3, "difficulty": 3, "date": "2024",
                "comment": "<script>alert(1)</script> great"}]
    html = professor_html(profile, reviews, "https://ratemyhusky.com/professors/x-y")
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


def test_professor_html_caps_reviews():
    profile = {
        "name": "X Y", "department": "CS", "avgRating": 3.0, "totalRatings": 50,
        "wouldTakeAgainPct": None, "difficulty": None, "rmpRating": None,
        "traceRating": None, "imageUrl": None, "professorUrl": None, "traceCourses": [],
    }
    reviews = [{"course": "CS1", "quality": 3, "difficulty": 3, "date": "2024",
                "comment": f"comment number {i}"} for i in range(50)]
    html = professor_html(profile, reviews, "https://ratemyhusky.com/professors/x-y")
    assert html.count("<blockquote>") == MAX_SNAPSHOT_REVIEWS


def test_course_html_has_title_h1_and_jsonld():
    detail = {
        "summary": {"code": "ECON1115", "name": "Macroeconomics",
                    "department": "Economics", "avgRating": 4.1,
                    "avgEnrollment": 120, "latestTermTitle": "Fall 2025"},
        "instructors": [{"name": "Francis Georges", "slug": "francis-georges"}],
    }
    html = course_html(detail, "https://ratemyhusky.com/courses/econ1115")
    assert "<title>ECON1115 — Macroeconomics at Northeastern | RateMyHusky</title>" in html
    assert "<h1>ECON1115 — Macroeconomics</h1>" in html
    block = _extract_jsonld(html)[0]
    assert block["@type"] == "Course"
    assert block["courseCode"] == "ECON1115"


def test_not_found_html_is_noindex():
    html = not_found_html("professor")
    assert '<meta name="robots" content="noindex">' in html


def test_render_professor_route_returns_html(render_client):
    resp = render_client.get("/render/professors/francis-georges")
    assert resp.status_code == 200
    assert resp.mimetype == "text/html"
    body = resp.get_data(as_text=True)
    assert "<h1>Francis Georges — Economics at Northeastern University</h1>" in body
    assert "Excellent lecturer." in body
    assert resp.headers["Cache-Control"] == "public, max-age=3600, s-maxage=86400"


def test_render_professor_missing_is_404_noindex(render_client):
    resp = render_client.get("/render/professors/missing")
    assert resp.status_code == 404
    assert '<meta name="robots" content="noindex">' in resp.get_data(as_text=True)


def test_render_course_route_returns_html(render_client):
    resp = render_client.get("/render/courses/econ1115")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "<h1>ECON1115 — Macroeconomics</h1>" in body
    assert resp.headers["Cache-Control"] == "public, max-age=3600, s-maxage=86400"


# ── TRACE review count (number only, never the comment text) ──

def _base_profile(**over):
    p = {
        "name": "Francis Georges", "department": "Economics", "avgRating": 4.25,
        "totalRatings": 2686, "wouldTakeAgainPct": 83, "difficulty": 2.9,
        "rmpRating": None, "traceRating": None, "imageUrl": None,
        "professorUrl": None, "traceCourses": [],
    }
    p.update(over)
    return p


def test_professor_html_shows_trace_review_count():
    html = professor_html(_base_profile(), [], "https://ratemyhusky.com/professors/x",
                          trace_count=5662)
    assert "<dt>TRACE reviews</dt><dd>5662</dd>" in html


def test_professor_html_omits_trace_count_when_zero():
    html = professor_html(_base_profile(), [], "https://ratemyhusky.com/professors/x",
                          trace_count=0)
    assert "TRACE reviews" not in html


def test_professor_html_never_shows_gated_trace_comment_text():
    # Even if a gated (empty-text) trace entry were somehow passed as a review,
    # only RMP review blockquotes are rendered; the trace count is a number only.
    html = professor_html(_base_profile(), [], "https://ratemyhusky.com/professors/x",
                          trace_count=42)
    # The count appears, but no blockquote section exists (no RMP review text given).
    assert "<dd>42</dd>" in html
    assert "<blockquote>" not in html


def test_professor_html_shows_rmp_review_count():
    reviews = [{"course": "ECON1115", "quality": 5, "difficulty": 3,
                "date": "2024", "comment": "Great."}]
    html = professor_html(_base_profile(), reviews, "https://ratemyhusky.com/professors/x",
                          trace_count=10)
    assert "<dt>RateMyProfessor reviews</dt><dd>1</dd>" in html


# ── Social link-preview meta tags ──

def test_professor_html_has_twitter_card_large_image_when_photo():
    html = professor_html(_base_profile(imageUrl="https://img/x.jpg"), [],
                          "https://ratemyhusky.com/professors/x")
    assert '<meta name="twitter:card" content="summary_large_image">' in html
    assert '<meta name="twitter:title" content="Francis Georges — Economics at Northeastern | RateMyHusky">' in html
    assert '<meta name="twitter:image" content="https://img/x.jpg">' in html
    assert '<meta property="og:image:alt"' in html


def test_professor_html_twitter_summary_when_no_photo():
    html = professor_html(_base_profile(imageUrl=None), [],
                          "https://ratemyhusky.com/professors/x")
    # Falls back to the bare logo → small summary card, not large image.
    assert '<meta name="twitter:card" content="summary">' in html


def test_course_html_has_twitter_tags():
    detail = {
        "summary": {"code": "ECON1115", "name": "Macroeconomics",
                    "department": "Economics", "avgRating": 4.1,
                    "avgEnrollment": 120, "latestTermTitle": "Fall 2025"},
        "instructors": [],
    }
    html = course_html(detail, "https://ratemyhusky.com/courses/econ1115")
    assert '<meta name="twitter:card"' in html
    assert '<meta name="twitter:title" content="ECON1115 — Macroeconomics at Northeastern | RateMyHusky">' in html


def test_render_professor_route_exposes_trace_count(render_client):
    # The conftest stub returns traceComments with entries; the route should
    # surface their count without rendering any gated comment text.
    resp = render_client.get("/render/professors/francis-georges")
    body = resp.get_data(as_text=True)
    assert "TRACE reviews" in body
