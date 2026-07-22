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


import os
import re
import csv
import sys
import argparse

import numpy as np
import cv2
import requests

try:
    import truststore
    truststore.inject_into_ssl()  # coe.northeastern.edu serves an incomplete cert chain; OS trust store resolves it
except ImportError:
    pass

# NOTE: mp.solutions (legacy Solutions API) requires mediapipe<=0.10.14 — later
# releases dropped it in favor of the Tasks API, which silently leaves _mp_face None.
try:
    import mediapipe as mp
    _mp_face = mp.solutions.face_detection.FaceDetection(
        model_selection=1, min_detection_confidence=0.5
    )
except Exception as e:
    _mp_face = None
    print(f"WARNING: mediapipe unavailable ({e}); falling back to Haar profile cascade only", file=sys.stderr)

_PROFILE_CASCADE = cv2.CascadeClassifier(
    cv2.data.haarcascades + "haarcascade_profileface.xml"
)


def upgrade_image_url(url):
    """Mirror precompute.upgrade_image_url — analyze the image the frontend displays."""
    return re.sub(r"-\d+x\d+(?=\.\w+$)", "", str(url))


def detect_faces(image_bgr):
    """Return [Face] in pixel coords. Frontal (mediapipe) first, then profile fallback."""
    h, w = image_bgr.shape[:2]
    faces = []

    if _mp_face is not None:
        rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
        res = _mp_face.process(rgb)
        if res.detections:
            for det in res.detections:
                box = det.location_data.relative_bounding_box
                fx = int(box.xmin * w)
                fy = int(box.ymin * h)
                fw = int(box.width * w)
                fh = int(box.height * h)
                if fw > 0 and fh > 0:
                    faces.append(Face(max(fx, 0), max(fy, 0), fw, fh))

    if not faces:
        gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
        for (px, py, pw, ph) in _PROFILE_CASCADE.detectMultiScale(gray, 1.1, 5):
            faces.append(Face(int(px), int(py), int(pw), int(ph)))
        if not faces:
            # the cascade only detects left-facing profiles; mirror for right-facing
            flipped = cv2.flip(gray, 1)
            for (px, py, pw, ph) in _PROFILE_CASCADE.detectMultiScale(flipped, 1.1, 5):
                faces.append(Face(int(w - px - pw), int(py), int(pw), int(ph)))

    return faces


def fetch_image(url, session):
    """Download and decode an image to a BGR ndarray. Returns None on failure."""
    try:
        resp = session.get(url, timeout=15)
        if resp.status_code != 200 or not resp.content:
            return None
        arr = np.frombuffer(resp.content, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        return img
    except Exception:
        return None


def process_row(row, session):
    """Add focus_x, focus_y, _bucket, _num_faces to a CSV row dict."""
    url = (row.get("image_url") or "").strip()
    if not url:
        row["focus_x"], row["focus_y"] = DEFAULT_FOCUS
        row["_bucket"], row["_num_faces"] = "no_face", 0
        return row

    img = fetch_image(upgrade_image_url(url), session)
    if img is None:
        fx, fy, bucket, n = DEFAULT_FOCUS[0], DEFAULT_FOCUS[1], "no_face", 0
    else:
        h, w = img.shape[:2]
        faces = detect_faces(img)
        fx, fy, bucket, n = select_focus(faces, w, h)

    row["focus_x"], row["focus_y"] = round(fx, 1), round(fy, 1)
    row["_bucket"], row["_num_faces"] = bucket, n
    return row


def _make_session():
    s = requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0 (compatible; PhotoFocus/1.0)"})
    return s


def main():
    parser = argparse.ArgumentParser(description="Compute face focal points for headshots")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("-o", "--output", type=str, default=None)
    parser.add_argument("--input", type=str, default=None)
    args = parser.parse_args()
    if args.limit and not args.output:
        parser.error("--limit requires -o/--output (refusing to overwrite the source CSV with a truncated subset)")

    script_dir = os.path.dirname(os.path.abspath(__file__))
    data_dir = os.path.join(script_dir, "output_data")
    in_path = args.input or os.path.join(data_dir, "professor_photos.csv")
    out_path = args.output or in_path
    review_path = os.path.join(data_dir, "focus_review.csv")

    with open(in_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if args.limit:
        rows = rows[: args.limit]

    session = _make_session()
    processed, review = [], []
    confident = 0
    for i, row in enumerate(rows, 1):
        r = process_row(row, session)
        processed.append(r)
        if r["_bucket"] == "confident":
            confident += 1
        elif (row.get("image_url") or "").strip():
            review.append({
                "name": r.get("name", ""),
                "image_url": r.get("image_url", ""),
                "reason": r["_bucket"],
                "num_faces": r["_num_faces"],
            })
        if i % 100 == 0:
            print(f"  {i}/{len(rows)} processed ({confident} confident)")

    base_fields = ["name", "image_url", "source_page"]
    out_fields = base_fields + ["focus_x", "focus_y"]
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=out_fields, extrasaction="ignore")
        writer.writeheader()
        for r in processed:
            writer.writerow(r)

    with open(review_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["name", "image_url", "reason", "num_faces"])
        writer.writeheader()
        writer.writerows(review)

    print(f"  Wrote {len(processed)} rows to {out_path}")
    print(f"  {confident} confident, {len(review)} flagged in {review_path}")


if __name__ == "__main__":
    main()
