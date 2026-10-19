"""photo_thumbs: file names match the frontend's, and thumbnails are small."""

import io

from PIL import Image

import photo_thumbs

# Computed with the reference cyrb53 in JavaScript; frontend/src/utils/photoThumb.selftest.ts
# checks the same vectors, so the two sides name every file the same way.
VECTORS = [
    ("", "bdcb81aee8d83"),
    ("a", "1c2ba782c97901"),
    ("https://cssh.northeastern.edu/wp-content/uploads/2020/01/Block-Aaron-414-x-512-px.png", "4fd4a9c944f20"),
    ("https://example.com/José-😀.jpg", "18a7399017e0d"),
]


def test_thumb_name_matches_javascript():
    for url, expected in VECTORS:
        assert photo_thumbs.thumb_name(url) == f"{expected}.webp", url


def _png(w, h, mode="RGB"):
    buf = io.BytesIO()
    Image.new(mode, (w, h), (200, 100, 50, 255) if mode == "RGBA" else (200, 100, 50)).save(buf, "PNG")
    return buf.getvalue()


def test_shrinks_short_side_and_keeps_aspect():
    out = Image.open(io.BytesIO(photo_thumbs.make_thumbnail(_png(1400, 933))))
    assert out.format == "WEBP"
    assert min(out.size) == photo_thumbs.SHORT_SIDE
    assert abs(out.size[0] / out.size[1] - 1400 / 933) < 0.01


def test_never_upscales_small_photos():
    out = Image.open(io.BytesIO(photo_thumbs.make_thumbnail(_png(200, 250))))
    assert out.size == (200, 250)


def test_flattens_transparency():
    out = Image.open(io.BytesIO(photo_thumbs.make_thumbnail(_png(800, 800, "RGBA"))))
    assert out.mode == "RGB"
