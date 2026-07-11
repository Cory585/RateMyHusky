"""
Compute per-professor face focal points for circular avatar cropping.

Reads professor_photos.csv, downloads each image, runs face detection, and
writes focus_x / focus_y percentage columns plus focus_review.csv for the
ambiguous / no-face cases. Images are fetched transiently for detection only;
the stored URL is unchanged.

Usage:
    python compute_focus.py
    python compute_focus.py --limit 50
    python compute_focus.py -o output_data/professor_photos.csv
"""

from collections import namedtuple

DEFAULT_FOCUS = (50.0, 30.0)   # (x%, y%) for ambiguous / no-face
HEADROOM_FRAC = 0.10           # shift focus up by 10% of image height
AMBIGUITY_RATIO = 1.35         # best must beat second by this factor to be confident

Face = namedtuple("Face", "x y w h")


def _clamp(v, lo=0.0, hi=100.0):
    return max(lo, min(hi, v))


def score_face(face, img_w, img_h):
    """area * centrality. Centrality is 1.0 at image center, decaying to ~0 at corners."""
    area = face.w * face.h
    cx = face.x + face.w / 2.0
    cy = face.y + face.h / 2.0
    # normalized distance from center, 0 at center, 1 at a corner
    dx = (cx - img_w / 2.0) / (img_w / 2.0)
    dy = (cy - img_h / 2.0) / (img_h / 2.0)
    dist = (dx * dx + dy * dy) ** 0.5 / (2 ** 0.5)
    centrality = 1.0 - dist
    return area * max(centrality, 0.01)


def select_focus(faces, img_w, img_h):
    """Pick the professor's face and return (focus_x%, focus_y%, bucket, num_faces)."""
    n = len(faces)
    if n == 0:
        return DEFAULT_FOCUS[0], DEFAULT_FOCUS[1], "no_face", 0

    scored = sorted(faces, key=lambda f: score_face(f, img_w, img_h), reverse=True)
    best = scored[0]

    if n >= 2:
        s_best = score_face(best, img_w, img_h)
        s_second = score_face(scored[1], img_w, img_h)
        if s_second > 0 and s_best < AMBIGUITY_RATIO * s_second:
            return DEFAULT_FOCUS[0], DEFAULT_FOCUS[1], "ambiguous", n

    cx = best.x + best.w / 2.0
    cy = best.y + best.h / 2.0
    fx = _clamp(cx / img_w * 100.0)
    fy = _clamp((cy / img_h - HEADROOM_FRAC) * 100.0)
    return fx, fy, "confident", n
