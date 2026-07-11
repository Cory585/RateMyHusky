import os, sys
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__) + "/.."))

import compute_focus as cf
from compute_focus import Face


def test_no_faces_returns_default_no_face_bucket():
    fx, fy, bucket, n = cf.select_focus([], 800, 600)
    assert (fx, fy) == cf.DEFAULT_FOCUS
    assert bucket == "no_face"
    assert n == 0


def test_single_centered_face_is_confident_and_centered_x():
    # 800x1000 portrait, face 200x200 centered horizontally near top third
    face = Face(x=300, y=200, w=200, h=200)  # center at (400, 300)
    fx, fy, bucket, n = cf.select_focus([face], 800, 1000)
    assert bucket == "confident"
    assert n == 1
    assert abs(fx - 50.0) < 0.01          # 400/800 = 50%
    # face center y = 300/1000 = 30%, minus headroom (10% of height) = 20%
    assert abs(fy - 20.0) < 0.01


def test_focus_y_clamped_to_zero_when_face_at_very_top():
    face = Face(x=300, y=0, w=200, h=50)   # center y = 25, headroom pushes negative
    fx, fy, bucket, n = cf.select_focus([face], 800, 1000)
    assert fy == 0.0


def test_prof_with_baby_picks_larger_face():
    prof = Face(x=300, y=200, w=300, h=300)   # big, center (450, 350)
    baby = Face(x=120, y=420, w=90, h=90)     # small
    fx, fy, bucket, n = cf.select_focus([prof, baby], 900, 900)
    assert bucket == "confident"
    assert n == 2
    assert abs(fx - (450 / 900 * 100)) < 0.01


def test_two_comparable_adults_is_ambiguous():
    left = Face(x=100, y=300, w=220, h=220)   # center (210, 410)
    right = Face(x=560, y=300, w=220, h=220)  # center (670, 410), mirror -> same score
    fx, fy, bucket, n = cf.select_focus([left, right], 880, 880)
    assert bucket == "ambiguous"
    assert (fx, fy) == cf.DEFAULT_FOCUS
    assert n == 2


def test_centrality_breaks_tie_toward_center_face_when_size_equal():
    centered = Face(x=340, y=340, w=200, h=200)  # center (440, 440) ~ image center
    cornered = Face(x=0, y=0, w=200, h=200)      # center (100, 100)
    s_c = cf.score_face(centered, 880, 880)
    s_k = cf.score_face(cornered, 880, 880)
    assert s_c > s_k
