import io
import zipfile

import pytest

from docqa.limits import RateLimiter, TTLSet, check_docx_safe


def test_rate_limiter_window():
    t = [0.0]
    r = RateLimiter(2, 60, clock=lambda: t[0])
    assert r.allow("u") and r.allow("u") and not r.allow("u")
    assert r.allow("other")
    t[0] = 61
    assert r.allow("u")


def test_rate_limiter_bounds_keys_and_prunes_stale_entries():
    t = [0.0]
    r = RateLimiter(10, 60, clock=lambda: t[0], max_keys=2)
    assert r.allow("a")
    assert r.allow("b")
    assert r.allow("c")
    assert len(r._events) <= 2
    t[0] = 61
    assert r.allow("d")
    assert len(r._events) == 1


def test_ttlset_expires():
    t = [0.0]
    s = TTLSet(10, clock=lambda: t[0])
    s.add("k")
    assert "k" in s
    t[0] = 11
    assert "k" not in s


def test_docx_bomb_and_invalid():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", b"0" * (210 * 1024 * 1024))
    with pytest.raises(ValueError):
        check_docx_safe(buf.getvalue())
    with pytest.raises(ValueError):
        check_docx_safe(b"nope")
    ok = io.BytesIO()
    with zipfile.ZipFile(ok, "w") as z:
        z.writestr("a.xml", b"x")
    check_docx_safe(ok.getvalue())
