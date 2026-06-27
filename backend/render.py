"""Static HTML snapshots of professor/course pages for AI/search crawlers.

Pure HTML builders here have no Flask or DB dependencies so they can be unit
tested directly. The blueprint routes (added in a later task) wire these to
the existing API data functions.
"""

import json
from html import escape as _html_escape
from flask import Blueprint  # noqa: F401  (used by the route task)

MAX_SNAPSHOT_REVIEWS = 15
SITE = "https://ratemyhusky.com"


def _esc(value) -> str:
    """HTML-escape a value; None -> ''. Quotes escaped for attribute safety."""
    if value is None:
        return ""
    return _html_escape(str(value), quote=True)


def _jsonld_script(obj) -> str:
    # Escape '<' so a value can't break out of the <script> tag.
    payload = json.dumps(obj).replace("<", "\\u003c")
    return f'<script type="application/ld+json">{payload}</script>'


def _page(title: str, description: str, canonical: str, body: str,
          jsonld: list, image: str | None = None, noindex: bool = False,
          og_type: str = "website", image_alt: str | None = None) -> str:
    robots = '<meta name="robots" content="noindex">' if noindex else \
             '<meta name="robots" content="index, follow">'
    # A real, content-bearing image gives social platforms a large card; the
    # bare logo is only a last-resort fallback.
    has_real_image = bool(image)
    img = image or f"{SITE}/logo.jpg"
    alt = image_alt or title
    twitter_card = "summary_large_image" if has_real_image else "summary"
    scripts = "\n".join(_jsonld_script(b) for b in jsonld)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{_esc(title)}</title>
<meta name="description" content="{_esc(description)}">
<link rel="canonical" href="{_esc(canonical)}">
{robots}
<meta property="og:site_name" content="RateMyHusky">
<meta property="og:type" content="{_esc(og_type)}">
<meta property="og:title" content="{_esc(title)}">
<meta property="og:description" content="{_esc(description)}">
<meta property="og:url" content="{_esc(canonical)}">
<meta property="og:image" content="{_esc(img)}">
<meta property="og:image:alt" content="{_esc(alt)}">
<meta name="twitter:card" content="{twitter_card}">
<meta name="twitter:title" content="{_esc(title)}">
<meta name="twitter:description" content="{_esc(description)}">
<meta name="twitter:image" content="{_esc(img)}">
<meta name="twitter:image:alt" content="{_esc(alt)}">
{scripts}
</head>
<body>
{body}
</body>
</html>
"""


def _stat_rows(pairs) -> str:
    rows = []
    for label, value in pairs:
        if value is None or value == "":
            continue
        rows.append(f"<dt>{_esc(label)}</dt><dd>{_esc(value)}</dd>")
    return "<dl>" + "".join(rows) + "</dl>" if rows else ""


def professor_html(profile: dict, reviews: list, canonical: str,
                   trace_count: int = 0) -> str:
    name = profile.get("name") or ""
    dept = profile.get("department") or ""
    avg = profile.get("avgRating") or 0
    total = profile.get("totalRatings") or 0
    wta = profile.get("wouldTakeAgainPct")
    diff = profile.get("difficulty")
    rmp_count = len(reviews)

    title = f"{name} — {dept} at Northeastern | RateMyHusky"
    wta_txt = f", {wta}% would take again" if wta is not None else ""
    summary = (
        f"{name} teaches {dept} at Northeastern University. "
        f"Average rating {avg}/5 across {total} ratings{wta_txt}. "
        f"Data is aggregated from TRACE evaluations and RateMyProfessor reviews."
    )

    stats = _stat_rows([
        ("Average rating", f"{avg}/5"),
        ("Total ratings", total),
        ("Would take again", f"{wta}%" if wta is not None else None),
        ("Difficulty", f"{diff}/5" if diff is not None else None),
        ("RateMyProfessor rating", profile.get("rmpRating")),
        ("TRACE rating", profile.get("traceRating")),
        # Count of TRACE evaluations only — the comment text stays gated.
        ("TRACE reviews", trace_count if trace_count else None),
        ("RateMyProfessor reviews", rmp_count if rmp_count else None),
    ])

    courses = profile.get("traceCourses") or []
    course_items = "".join(
        f"<li>{_esc(c.get('displayName'))}</li>" for c in courses if c.get("displayName")
    )
    courses_block = f"<h2>Courses taught</h2><ul>{course_items}</ul>" if course_items else ""

    review_items = []
    for r in reviews[:MAX_SNAPSHOT_REVIEWS]:
        comment = (r.get("comment") or "").strip()
        if not comment:
            continue
        meta = " ".join(x for x in [_esc(r.get("course")), _esc(r.get("date"))] if x)
        review_items.append(f"<blockquote>{_esc(comment)}<cite>{meta}</cite></blockquote>")
    reviews_block = ("<h2>Student reviews</h2>" + "".join(review_items)) if review_items else ""

    body = (
        f"<h1>{_esc(name)} — {_esc(dept)} at Northeastern University</h1>"
        f"<p>{_esc(summary)}</p>"
        f"{stats}{courses_block}{reviews_block}"
        f'<p><a href="{_esc(canonical)}">View on RateMyHusky</a></p>'
    )

    jsonld = {
        "@context": "https://schema.org",
        "@type": "Person",
        "name": name,
        "jobTitle": "Professor",
        "worksFor": {"@type": "CollegeOrUniversity", "name": "Northeastern University"},
    }
    if profile.get("imageUrl"):
        jsonld["image"] = profile["imageUrl"]
    if total and total > 0:
        jsonld["aggregateRating"] = {
            "@type": "AggregateRating",
            "ratingValue": f"{avg:.2f}" if isinstance(avg, (int, float)) else str(avg),
            "ratingCount": total,
            "bestRating": "5",
            "worstRating": "1",
        }

    return _page(title, summary, canonical, body, [jsonld],
                 image=profile.get("imageUrl"), og_type="profile",
                 image_alt=f"{name}, professor of {dept} at Northeastern University")


def course_html(detail: dict, canonical: str) -> str:
    s = detail.get("summary") or {}
    code = s.get("code") or ""
    cname = s.get("name") or ""
    dept = s.get("department") or ""
    avg = s.get("avgRating")
    last = s.get("latestTermTitle") or ""

    title = f"{code} — {cname} at Northeastern | RateMyHusky"
    avg_txt = f"Average rating {avg}/5. " if avg is not None else ""
    last_txt = f"Last taught {last}. " if last else ""
    summary = (
        f"{code} — {cname} ({dept}) at Northeastern University. "
        f"{avg_txt}{last_txt}"
        f"Compare instructors using TRACE evaluations and RateMyProfessor reviews."
    )

    stats = _stat_rows([
        ("Average rating", f"{avg}/5" if avg is not None else None),
        ("Average enrollment", s.get("avgEnrollment")),
        ("Last taught", last),
    ])

    instructors = detail.get("instructors") or []
    inst_items = "".join(
        f'<li><a href="{SITE}/professors/{_esc(i.get("slug"))}">{_esc(i.get("name"))}</a></li>'
        for i in instructors if i.get("slug")
    )
    inst_block = f"<h2>Instructors</h2><ul>{inst_items}</ul>" if inst_items else ""

    body = (
        f"<h1>{_esc(code)} — {_esc(cname)}</h1>"
        f"<p>{_esc(summary)}</p>"
        f"{stats}{inst_block}"
        f'<p><a href="{_esc(canonical)}">View on RateMyHusky</a></p>'
    )

    jsonld = {
        "@context": "https://schema.org",
        "@type": "Course",
        "name": f"{code} — {cname}",
        "courseCode": code,
        "provider": {"@type": "CollegeOrUniversity", "name": "Northeastern University"},
    }
    return _page(title, summary, canonical, body, [jsonld])


def home_html(stats: list, top_professors: list, canonical: str) -> str:
    title = "RateMyHusky — Northeastern University professor & course ratings"
    summary = (
        "RateMyHusky aggregates TRACE course evaluations and RateMyProfessor "
        "reviews for Northeastern University professors and courses. Browse "
        "ratings, difficulty, and student comments to plan your schedule — free."
    )

    stat_rows = _stat_rows([(s.get("label"), s.get("value")) for s in (stats or [])])

    prof_li = []
    for p in (top_professors or []):
        if not p.get("slug"):
            continue
        rating = p.get("avgRating")
        suffix = f" ({_esc(rating)}/5)" if rating is not None else ""
        prof_li.append(
            f'<li><a href="{SITE}/professors/{_esc(p.get("slug"))}">{_esc(p.get("name"))}</a>'
            f' — {_esc(p.get("department"))}{suffix}</li>'
        )
    prof_items = "".join(prof_li)
    profs_block = (
        f"<h2>Top-rated professors</h2><ul>{prof_items}</ul>" if prof_items else ""
    )

    body = (
        f"<h1>{_esc(title)}</h1>"
        f"<p>{_esc(summary)}</p>"
        f"{stat_rows}{profs_block}"
        f'<p>Browse all <a href="{SITE}/professors">professors</a> and '
        f'<a href="{SITE}/courses">courses</a>.</p>'
    )

    jsonld = {
        "@context": "https://schema.org",
        "@type": "WebSite",
        "name": "RateMyHusky",
        "url": SITE,
        "potentialAction": {
            "@type": "SearchAction",
            "target": f"{SITE}/professors?q={{search_term_string}}",
            "query-input": "required name=search_term_string",
        },
    }
    return _page(title, summary, canonical, body, [jsonld])


def not_found_html(kind: str) -> str:
    body = f"<h1>{_esc(kind.title())} not found</h1>"
    return _page("Not found | RateMyHusky", "Not found", f"{SITE}/", body, [],
                 noindex=True)


render_bp = Blueprint("render", __name__)


# Lazy accessors so tests can monkeypatch and routes avoid circular imports.
def _get_profile_view():
    from server import professor_profile
    return professor_profile


def _get_reviews_view():
    from server import professor_reviews
    return professor_reviews


def _get_course_view():
    from server import course_profile  # the /api/courses/<code> view (server.py:1651)
    return course_profile


def _json_or_404(resp):
    """Return (data, None) on success or (None, status) when the view returned
    an error tuple."""
    if isinstance(resp, tuple):
        return None, resp[1]
    return resp.get_json(), None


@render_bp.route("/render/professors/<slug>")
def render_professor(slug):
    from flask import Response
    profile_resp = _get_profile_view()(slug)
    data, err = _json_or_404(profile_resp)
    if err:
        return Response(not_found_html("professor"), status=404, mimetype="text/html")

    reviews_resp = _get_reviews_view()(slug)
    rdata, rerr = _json_or_404(reviews_resp)
    reviews = (rdata or {}).get("reviews", []) if not rerr else []
    # TRACE evaluation count (comment text stays gated; we expose only the number).
    trace_count = len((rdata or {}).get("traceComments", [])) if not rerr else 0

    canonical = f"{SITE}/professors/{slug}"
    html = professor_html(data, reviews, canonical, trace_count=trace_count)
    resp = Response(html, mimetype="text/html")
    resp.headers["Cache-Control"] = "public, max-age=3600, s-maxage=86400"
    return resp


@render_bp.route("/render/courses/<code>")
def render_course(code):
    from flask import Response
    detail_resp = _get_course_view()(code)
    data, err = _json_or_404(detail_resp)
    if err:
        return Response(not_found_html("course"), status=404, mimetype="text/html")

    canonical = f"{SITE}/courses/{code}"
    html = course_html(data, canonical)
    resp = Response(html, mimetype="text/html")
    resp.headers["Cache-Control"] = "public, max-age=3600, s-maxage=86400"
    return resp
