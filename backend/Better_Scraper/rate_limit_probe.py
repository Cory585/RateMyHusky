"""
Rate limit probe for ratemyprofessors.com GraphQL API.

Tests BOTH the teacher-search query AND the ratings/reviews query
at increasing concurrency levels to find the real throughput ceiling.

Usage:
    python rate_limit_probe.py
    python rate_limit_probe.py --school-id 696 --requests-per-level 20
"""

import argparse
import base64
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from statistics import median

import requests

RMP_GRAPHQL_URL = "https://www.ratemyprofessors.com/graphql"
RMP_BASE_URL = "https://www.ratemyprofessors.com"

TEACHER_SEARCH_QUERY = """
query TeacherSearchPaginationQuery(
    $count: Int!,
    $cursor: String,
    $query: TeacherSearchQuery!
) {
    search: newSearch {
        teachers(query: $query, first: $count, after: $cursor) {
            didFallback
            edges {
                cursor
                node {
                    id
                    legacyId
                    firstName
                    lastName
                    department
                    school { id name }
                    avgRating
                    numRatings
                    avgDifficulty
                    wouldTakeAgainPercent
                }
            }
            pageInfo { hasNextPage endCursor }
        }
    }
}
"""

TEACHER_RATINGS_QUERY = """
query TeacherRatingsPageQuery(
    $id: ID!,
    $count: Int!,
    $cursor: String
) {
    node(id: $id) {
        ... on Teacher {
            ratings(first: $count, after: $cursor) {
                edges {
                    node {
                        comment
                        class
                        date
                        qualityRating
                        difficultyRatingRounded
                        ratingTags
                        grade
                        isForOnlineClass
                        attendanceMandatory
                        textbookIsUsed
                    }
                }
                pageInfo { hasNextPage endCursor }
            }
        }
    }
}
"""

WORKER_LEVELS = [1, 2, 3, 5, 8, 10, 15, 20]
CONSECUTIVE_ERROR_LIMIT = 5


def make_session(school_id: int) -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/145.0.0.0 Safari/537.36"
        ),
        "Referer": "https://www.ratemyprofessors.com/",
        "Origin": "https://www.ratemyprofessors.com",
        "Content-Type": "application/json",
        "Authorization": "Basic dGVzdDp0ZXN0",
    })
    try:
        resp = session.get(f"{RMP_BASE_URL}/school/{school_id}", timeout=15)
        resp.raise_for_status()
        print(f"  ✓ Session ready ({len(session.cookies)} cookies)")
    except Exception as e:
        print(f"  ⚠ Cookie fetch failed: {e} — trying without cookies")
    return session


class LevelResult:
    def __init__(self, num_workers: int, wall_time: float):
        self.num_workers = num_workers
        self.wall_time = wall_time
        self.latencies: list[float] = []
        self.statuses: list[int] = []
        self.errors: list[str] = []

    def add(self, status: int, latency: float, error: str = ""):
        self.latencies.append(latency)
        self.statuses.append(status)
        if error:
            self.errors.append(error)

    @property
    def total(self) -> int:
        return len(self.latencies)

    @property
    def success_count(self) -> int:
        return sum(1 for s in self.statuses if s == 200)

    @property
    def error_count(self) -> int:
        return self.total - self.success_count

    @property
    def error_pct(self) -> float:
        return round(self.error_count / self.total * 100, 2) if self.total else 0

    @property
    def throughput(self) -> float:
        return round(self.success_count / self.wall_time * 60, 1) if self.wall_time else 0

    def summary(self) -> dict:
        sorted_lat = sorted(self.latencies)
        n = len(sorted_lat)
        p50 = median(sorted_lat) if n else 0
        p95 = sorted_lat[int(n * 0.95)] if n >= 20 else (sorted_lat[-1] if n else 0)
        p99 = sorted_lat[int(n * 0.99)] if n >= 100 else (sorted_lat[-1] if n else 0)
        status_dist = dict(Counter(self.statuses))
        return {
            "workers": self.num_workers,
            "total": self.total,
            "success": self.success_count,
            "errors": self.error_count,
            "error_pct": self.error_pct,
            "p50_ms": round(p50 * 1000, 1),
            "p95_ms": round(p95 * 1000, 1),
            "p99_ms": round(p99 * 1000, 1),
            "throughput_req_min": self.throughput,
            "status_distribution": status_dist,
        }


def collect_professor_ids(session, graphql_school_id, max_pages=5) -> list[str]:
    """Paginate through the professor list and collect graphql IDs."""
    ids: list[str] = []
    cursor: str | None = None
    page = 0
    while page < max_pages:
        payload = {
            "query": TEACHER_SEARCH_QUERY,
            "variables": {
                "count": 1000,
                "cursor": cursor or "",
                "query": {"text": "", "schoolID": graphql_school_id, "fallback": True},
            },
        }
        resp = session.post(RMP_GRAPHQL_URL, json=payload, timeout=30)
        data = resp.json()
        edges = data.get("data", {}).get("search", {}).get("teachers", {}).get("edges", [])
        for edge in edges:
            ids.append(edge["node"]["id"])
        page_info = data.get("data", {}).get("search", {}).get("teachers", {}).get("pageInfo", {})
        has_next = page_info.get("hasNextPage", False)
        cursor = page_info.get("endCursor")
        page += 1
        if not has_next or not edges:
            break
    return ids


def run_probe(
    session,
    make_payload,
    requests_per_level: int,
    label: str,
) -> list[LevelResult]:
    """Run the concurrency ramp for a given query type."""
    results: list[LevelResult] = []
    consecutive_errors = 0

    def do_request(payload) -> tuple:
        start = time.perf_counter()
        error = ""
        status = 0
        try:
            resp = session.post(RMP_GRAPHQL_URL, json=payload, timeout=30)
            status = resp.status_code
            if status != 200:
                error = f"HTTP {status}"
                if status == 429:
                    error += " (RATE LIMITED)"
                elif status == 403:
                    error += " (FORBIDDEN)"
        except requests.RequestException as e:
            error = str(e)
            status = -1
        return status, time.perf_counter() - start, error

    for nw in WORKER_LEVELS:
        print(f"\n  --- {nw} worker(s) ---")
        level_start = time.perf_counter()

        with ThreadPoolExecutor(max_workers=nw) as pool:
            futures = [pool.submit(do_request, make_payload(i)) for i in range(requests_per_level)]
            level_results = []
            for f in as_completed(futures):
                try:
                    status, lat, error = f.result()
                except Exception as e:
                    status, lat, error = -1, 0, str(e)
                level_results.append((status, lat, error))

        wall = time.perf_counter() - level_start
        lr = LevelResult(nw, wall)
        for status, lat, error in level_results:
            lr.add(status, lat, error)

        results.append(lr)

        s = lr.summary()
        print(f"    OK: {s['success']}/{s['total']}  |  "
              f"Err: {s['error_pct']}%  |  "
              f"P50: {s['p50_ms']}ms  P95: {s['p95_ms']}ms  "
              f"~{s['throughput_req_min']} req/min  "
              f"(wall: {round(wall, 2)}s)")

        if lr.error_count > 0:
            statuses_failed = [str(s) for s in lr.statuses if s != 200]
            counter = Counter(statuses_failed)
            print(f"    Error breakdown: {dict(counter)}")
            if all(s == -1 or s in (429, 403) for s in lr.statuses if s != 200):
                consecutive_errors = lr.error_count
            else:
                consecutive_errors = 0
        else:
            consecutive_errors = 0

        if consecutive_errors >= CONSECUTIVE_ERROR_LIMIT:
            print(f"\n  ⛔ {consecutive_errors} consecutive rate-limit errors — stopping {label} probe")
            break

        time.sleep(2)

    return results


def print_report(
    search_results: list[LevelResult],
    review_results: list[LevelResult] | None,
    search_label: str = "SEARCH",
    review_label: str = "REVIEWS",
):
    print("\n\n" + "=" * 100)
    print(f"  RATE LIMIT PROBE — RESULTS")
    print("=" * 100)

    sets = [(search_label, search_results)]
    if review_results:
        sets.append((review_label, review_results))

    for label, results in sets:
        print(f"\n  ── {label} ──")
        print(f"  {'Workers':>7} | {'OK/Total':>8} | "
              f"{'Err%':>5} | {'P50':>6} {'P95':>6} {'P99':>6} | "
              f"{'req/min':>7} | {'Wall':>5}")
        print(f"  {'───────':>7} | {'────────':>8} | "
              f"{'─────':>5} | {'──────':>6} {'──────':>6} {'──────':>6} | "
              f"{'───────':>7} | {'─────':>5}")
        for lr in results:
            s = lr.summary()
            print(
                f"  {s['workers']:>7d} | "
                f"{s['success']:>3d}/{s['total']:<3d}    | "
                f"{s['error_pct']:>4.1f}%  | "
                f"{s['p50_ms']:>6.1f} {s['p95_ms']:>6.1f} {s['p99_ms']:>6.1f} | "
                f"{s['throughput_req_min']:>7.1f} | "
                f"{round(lr.wall_time, 1):>5.1f}s"
            )

    # Combined recommendation
    print(f"\n{'─' * 100}")
    print(f"  COMPARISON & RECOMMENDATION")
    print(f"  {'─' * 40}")

    for label, results in sets:
        good = [r for r in results if r.error_pct < 1.0]
        if good:
            best = good[-1]
            bs = best.summary()
            est_total = 3270 if label == review_label else 3889
            est_min = round(est_total / bs["throughput_req_min"], 1) if bs["throughput_req_min"] else 999
            print(f"\n  {label}:")
            print(f"    Safe max_workers:     {bs['workers']}")
            print(f"    Measured throughput:  ~{bs['throughput_req_min']} req/min")
            print(f"    Est. full scrape:     ~{est_min} min ({est_total} req)")
        else:
            print(f"\n  {label}: ⚠ No level achieved <1% error rate")

    if search_results and review_results:
        sr_best = max((r for r in search_results if r.error_pct < 1.0), key=lambda r: r.throughput, default=None)
        rr_best = max((r for r in review_results if r.error_pct < 1.0), key=lambda r: r.throughput, default=None)
        if sr_best and rr_best:
            print(f"\n  ── BOTTLE NECK ──")
            slower = min(sr_best.throughput, rr_best.throughput)
            faster = max(sr_best.throughput, rr_best.throughput)
            ratio = round(faster / slower, 1) if slower else 1
            if rr_best.throughput < sr_best.throughput * 0.8:
                print(f"  ⚠ Review query is significantly SLOWER than search ({ratio}x)")
                print(f"     → Reviews are the bottleneck; use review throughput for your estimate")
            else:
                print(f"  ✓ Both queries perform similarly (ratio {ratio}x)")
                print(f"     → Use the lower throughput as your safe estimate")

    print(f"\n{'=' * 100}")
    print(f"  NOTE: This was run from a residential IP. GitHub Actions uses")
    print(f"  datacenter IPs (GCP/Azure) which may have different rate limits.")
    print(f"  Review queries load actual review data and may hit different")
    print(f"  backend rate limit tiers than teacher-search queries.")
    print(f"{'=' * 100}")


def main():
    parser = argparse.ArgumentParser(description="RMP rate limit probe")
    parser.add_argument("--school-id", type=int, default=696,
                        help="RMP school ID (default: 696 = Northeastern)")
    parser.add_argument("--requests-per-level", type=int, default=25,
                        help="Requests to send at each worker level (default: 25)")
    parser.add_argument("--reviews-only", action="store_true",
                        help="Skip search probe, only test reviews (requires saved IDs)")
    parser.add_argument("--professor-ids", type=str, default=None,
                        help="Comma-separated graphql IDs for review probe (skips collection)")
    args = parser.parse_args()

    print("RMP Rate Limit Probe")
    print(f"  School ID:           {args.school_id}")
    print(f"  Requests per level:  {args.requests_per_level}")
    print(f"  Worker levels:       {WORKER_LEVELS}")
    print(f"  Max consecutive err: {CONSECUTIVE_ERROR_LIMIT}")
    print()

    session = make_session(args.school_id)
    graphql_school_id = base64.b64encode(f"School-{args.school_id}".encode()).decode()

    # ── Step 1: Collect professor IDs for review probe ──
    professor_ids: list[str] = []
    if args.professor_ids:
        professor_ids = [x.strip() for x in args.professor_ids.split(",")]
        print(f"\n  Using provided {len(professor_ids)} professor IDs (skipping collection)")
    elif not args.reviews_only:
        print("\n  Collecting professor IDs for review probe...")
        professor_ids = collect_professor_ids(session, graphql_school_id, max_pages=5)
        print(f"  Collected {len(professor_ids)} professor graphql IDs")

    # ── Step 2: Search query probe ──
    search_results: list[LevelResult] = []
    if not args.reviews_only:
        print(f"\n{'=' * 60}")
        print("  PHASE 1: TEACHER SEARCH QUERY")
        print(f"{'=' * 60}")

        search_payload = {
            "query": TEACHER_SEARCH_QUERY,
            "variables": {
                "count": 1000,
                "cursor": "",
                "query": {"text": "", "schoolID": graphql_school_id, "fallback": True},
            },
        }

        search_results = run_probe(
            session,
            make_payload=lambda i: search_payload,
            requests_per_level=args.requests_per_level,
            label="search",
        )

        time.sleep(3)

    # ── Step 3: Reviews query probe ──
    review_results: list[LevelResult] = []
    if professor_ids:
        print(f"\n{'=' * 60}")
        print("  PHASE 2: TEACHER RATINGS QUERY (reviews)")
        print(f"{'=' * 60}")

        review_results = run_probe(
            session,
            make_payload=lambda i: {
                "query": TEACHER_RATINGS_QUERY,
                "variables": {
                    "id": professor_ids[i % len(professor_ids)],
                    "count": 100,
                    "cursor": "",
                },
            },
            requests_per_level=args.requests_per_level,
            label="reviews",
        )
    else:
        print("\n  Skipping review probe (no professor IDs)")

    # ── Report ──
    print_report(
        search_results,
        review_results,
        search_label="SEARCH (prof list)",
        review_label="REVIEWS (ratings)",
    )


if __name__ == "__main__":
    main()
