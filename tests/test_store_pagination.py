"""SupabaseStore._get pagination against a fake PostgREST (regression for 416 Range Not Satisfiable)."""
import pytest

from jh.store import SupabaseStore

MAX_ROWS = 1000  # Supabase default max-rows


class Resp:
    def __init__(self, data, status=200):
        self._d, self.status_code = data, status

    def json(self):
        return self._d

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"{self.status_code} Client Error")


class FakePostgREST:
    """Serves `total` rows. Honours limit/offset query params and a Range header the way PostgREST does
    (intersection of both windows), caps each response at max-rows, and answers 416 when the requested
    window starts past the end of the data or lies outside the limit window."""

    def __init__(self, total):
        self.rows = [{"id": i} for i in range(total)]
        self.headers = {}
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None):
        params, headers = dict(params or {}), dict(headers or {})
        self.calls.append((params, headers))
        lo = int(params.get("offset", 0))
        hi = lo + int(params["limit"]) - 1 if "limit" in params else float("inf")
        if "Range" in headers:
            a, b = headers["Range"].split("-")
            lo, hi = max(lo, int(a)), min(hi, int(b))
            if lo > hi:
                return Resp({"message": "Requested range not satisfiable"}, 416)
        if lo > len(self.rows):
            return Resp({"message": "Requested range not satisfiable"}, 416)
        hi = min(hi, lo + MAX_ROWS - 1)
        return Resp(self.rows[lo:int(hi) + 1] if hi != float("inf") else self.rows[lo:])


def store(total):
    sess = FakePostgREST(total)
    return SupabaseStore("https://p.supabase.co", "k", session=sess), sess


def ids(rows):
    return [r["id"] for r in rows]


@pytest.mark.parametrize("total", [0, 1, 999])
def test_total_below_page(total):
    s, sess = store(total)
    assert ids(s.board()) == list(range(total))
    assert len(sess.calls) == 1


def test_total_exactly_one_page():
    s, sess = store(1000)
    assert ids(s.board()) == list(range(1000))
    assert len(sess.calls) == 2 and sess.calls[1][0]["offset"] == "1000"   # empty 2nd page, no error


def test_total_between_page_and_limit():
    s, _ = store(1500)
    assert ids(s.board(limit=2000)) == list(range(1500))


def test_total_above_limit_caps_rows():
    s, sess = store(3358)
    rows = s.board(limit=2000)
    assert ids(rows) == list(range(2000))
    assert [c[0]["limit"] for c in sess.calls] == ["1000", "1000"]   # never asks for row 2000+
    assert all("Range" not in c[1] for c in sess.calls)


def test_odd_limit_last_page_trimmed():
    s, sess = store(3358)
    assert len(s.board(limit=1234)) == 1234
    assert [(c[0]["offset"], c[0]["limit"]) for c in sess.calls] == [("0", "1000"), ("1000", "234")]


def test_no_limit_dumps_everything():
    s, _ = store(3358)
    assert ids(s.dump("jobs")) == list(range(3358))


def test_previously_failing_pattern_reproduces_with_range_header():
    """The old Range-header loop with limit=2000 on a 3358-row table -> 416 on the 3rd request."""
    _, sess = store(3358)
    params = {"order": "first_seen_at.desc", "limit": "2000"}
    statuses = [sess.get("x", params=params, headers={"Range": f"{o}-{o + 999}"}).status_code
                for o in (0, 1000, 2000)]
    assert statuses == [200, 200, 416]
    s, _ = store(3358)
    assert len(s.board(limit=2000)) == 2000                         # new code: fine


def test_416_treated_as_end():
    s, sess = store(10)
    sess.get = lambda url, params=None, headers=None, timeout=None: Resp({}, 416)
    assert s._get("jobs") == []


def test_other_errors_still_raise():
    s, sess = store(10)
    sess.get = lambda url, params=None, headers=None, timeout=None: Resp({}, 500)
    with pytest.raises(RuntimeError):
        s._get("jobs")


def test_recent_page_n():
    s, sess = store(500)
    assert ids(s.recent("events", n=200)) == list(range(200))
    assert len(sess.calls) == 1 and sess.calls[0][0]["limit"] == "200"
    s, _ = store(3000)
    assert len(s.recent("events", n=2500)) == 2500             # n above max-rows now paginates


def test_single_row_callers():
    s, sess = store(3)
    assert s.get_application("x") == {"id": 0}
    s, _ = store(0)
    assert s.get_application("x") is None and s.cache_get("k") is None


def test_recent_jobs_index_paginates_stably():
    s, sess = store(2500)
    assert len(s.recent_jobs_index()) == 2500
    assert all(c[0]["order"] == "id.asc" for c in sess.calls)
