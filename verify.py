"""
MovieBox API – Verification Script v2.0
Run:  python verify.py  (server must be running on localhost:8000)
"""

import httpx
import json
import sys

BASE = "http://localhost:8000"
PASS  = "\033[92m✔\033[0m"
FAIL  = "\033[91m✘\033[0m"
WARN  = "\033[93m⚠\033[0m"
BOLD  = "\033[1m"
RESET = "\033[0m"
DIM   = "\033[2m"

# ─────────────────────────────────────────────────────────────────────────────
#  Counters
# ─────────────────────────────────────────────────────────────────────────────
_passed = _failed = _warned = 0

def _ok(msg):   global _passed; _passed += 1;  print(f"    {PASS} {msg}")
def _err(msg):  global _failed; _failed += 1;  print(f"    {FAIL} {msg}")
def _warn(msg): global _warned; _warned += 1;  print(f"    {WARN} {msg}")

def section(title):
    print(f"\n{BOLD}{'─'*60}{RESET}")
    print(f"{BOLD}  {title}{RESET}")
    print(f"{BOLD}{'─'*60}{RESET}")

def sub(title):
    print(f"\n  {DIM}┌─{RESET} {title}")

# ─────────────────────────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────────────────────────
def get(path, params=None, timeout=35):
    url = BASE + path
    r = httpx.get(url, params=params, timeout=timeout)
    return r

def check_movies(movies, label=""):
    total = len(movies)
    with_poster = sum(1 for m in movies if m.get("poster_url"))
    with_name   = sum(1 for m in movies if m.get("name"))
    print(f"    {DIM}movies: {total} | names: {with_name}/{total} | "
          f"posters: {with_poster}/{total}{RESET}")
    if movies:
        m = movies[0]
        poster_status = "YES" if m.get("poster_url") else "NULL"
        print(f"    {DIM}sample: name={str(m.get('name','?'))[:40]!r} "
              f"| poster={poster_status}{RESET}")
    if with_name < total:
        _warn(f"{label}: {total - with_name} movie(s) missing names")
    if with_poster == 0 and total > 0:
        _warn(f"{label}: no posters resolved")
    else:
        _ok(f"{label}: {total} items, {with_poster}/{total} posters")

def assert_keys(data, keys, label):
    missing = [k for k in keys if k not in data]
    if missing:
        _err(f"{label}: missing keys {missing}")
        return False
    _ok(f"{label}: required keys present {keys}")
    return True

def assert_status(r, expected=200, label=""):
    if r.status_code == expected:
        _ok(f"{label or r.url}: HTTP {r.status_code}")
        return True
    _err(f"{label or r.url}: expected HTTP {expected}, got {r.status_code}")
    return False

# ─────────────────────────────────────────────────────────────────────────────
#  SECTION 1 – Static / existing endpoints
# ─────────────────────────────────────────────────────────────────────────────
section("1 · STATIC ENDPOINTS")

STATIC = [
    "/",
    "/home",
    "/home/trending",
    "/home/hot",
    "/home/cinema",
    "/home/banner",
    "/home/sections",
    "/tv-series",
    "/movies",
    "/animation",
    "/ranking",
]

for path in STATIC:
    sub(path)
    try:
        r = get(path)
        data = r.json()
        status_ok = assert_status(r, label=path)
        if not status_ok:
            continue

        if path == "/":
            ep_count = sum(len(v) for v in data.get("endpoints", {}).values())
            _ok(f"endpoint catalogue: {ep_count} routes listed")

        elif path == "/home/banner":
            featured = data.get("featured", [])
            print(f"    featured items: {len(featured)}")
            if featured:
                f = featured[0]
                print(f"    sample: name={str(f.get('name','?'))[:40]!r} "
                      f"| poster={'YES' if f.get('poster_url') else 'NULL'}")
            _ok(f"banner: {len(featured)} featured item(s)")

        elif path == "/home/sections":
            secs = data.get("sections", [])
            for s in secs:
                print(f"    {DIM}- {s['name']!r} ({s['count']} movies){RESET}")
            _ok(f"sections list: {len(secs)} section(s)")

        elif "movies" in data and "section" in data:
            # Single section (trending / hot / cinema)
            print(f"    section: {data.get('section','?')!r}")
            check_movies(data["movies"], path)

        else:
            # Multi-section pages
            sections_data = data.get("sections", [])
            print(f"    total_sections: {len(sections_data)} "
                  f"| poster_map_size: {data.get('poster_map_size', '?')}")
            for s in sections_data:
                print(f"\n    [{s['section']!r}] {s['count']} movies")
                check_movies(s.get("movies", []), s["section"])

    except Exception as e:
        _err(f"{path} => {e}")


# ─────────────────────────────────────────────────────────────────────────────
#  SECTION 2 – Pagination
# ─────────────────────────────────────────────────────────────────────────────
section("2 · PAGINATION")

PAGINATED = [
    ("/movies",    {"page": 2, "limit": 5},  "Movies page 2, limit 5"),
    ("/tv-series", {"page": 2, "limit": 5},  "TV-Series page 2, limit 5"),
    ("/animation", {"page": 2, "limit": 5},  "Animation page 2, limit 5"),
    ("/movies",    {"page": 1, "limit": 10}, "Movies page 1, limit 10 (baseline)"),
]

for path, params, label in PAGINATED:
    sub(f"{label}  →  {path}?{'&'.join(f'{k}={v}' for k,v in params.items())}")
    try:
        r = get(path, params=params)
        if not assert_status(r, label=label):
            continue

        data = r.json()
        assert_keys(data, ["page", "per_page", "has_more"], label)

        page_val     = data.get("page")
        per_page_val = data.get("per_page")
        has_more     = data.get("has_more")
        total        = data.get("total", "?")

        print(f"    page={page_val}  per_page={per_page_val}  "
              f"has_more={has_more}  total={total}")

        # Verify the page echo matches what we requested
        if page_val == params["page"]:
            _ok(f"page echo correct ({page_val})")
        else:
            _err(f"page echo mismatch: got {page_val}, expected {params['page']}")

        if per_page_val == params["limit"]:
            _ok(f"per_page echo correct ({per_page_val})")
        else:
            _warn(f"per_page echo: got {per_page_val}, expected {params['limit']}")

        # Verify actual movie count in sections
        all_movies = []
        for s in data.get("sections", []):
            all_movies.extend(s.get("movies", []))
        if all_movies:
            check_movies(all_movies, label)
        else:
            _warn(f"{label}: no movies returned (end of catalogue?)")

    except Exception as e:
        _err(f"{label} => {e}")


# ─────────────────────────────────────────────────────────────────────────────
#  SECTION 3 – Search
# ─────────────────────────────────────────────────────────────────────────────
section("3 · SEARCH")

# 3a – Suggest
sub("/search/suggest?q=avatar")
try:
    r = get("/search/suggest", params={"q": "avatar"})
    assert_status(r, label="suggest")
    data = r.json()
    assert_keys(data, ["query", "suggestions"], "suggest")
    suggestions = data.get("suggestions", [])
    print(f"    suggestions ({len(suggestions)}): "
          f"{', '.join(repr(s) for s in suggestions[:5])}")
    if suggestions:
        _ok(f"suggest: {len(suggestions)} suggestion(s) returned")
    else:
        _warn("suggest: empty suggestions list")
except Exception as e:
    _err(f"/search/suggest => {e}")

# 3b – Full search
sub("/search?q=avatar")
try:
    r = get("/search", params={"q": "avatar"})
    assert_status(r, label="search")
    data = r.json()
    assert_keys(data, ["query", "count", "movies"], "search")
    movies = data.get("movies", [])
    print(f"    query={data.get('query')!r}  count={data.get('count')}")
    check_movies(movies, "search:avatar")
except Exception as e:
    _err(f"/search => {e}")


# ─────────────────────────────────────────────────────────────────────────────
#  SECTION 4 – Dynamic Deep-Link Chain
#  /search → slug → /detail → subjectId
#  → /detail/{slug}/episodes
#  → /api/stream/{subjectId}?detailPath={slug}
# ─────────────────────────────────────────────────────────────────────────────
section("4 · DYNAMIC DEEP-LINK CHAIN  (search → detail → episodes → stream)")

# ── Step 4.0: find a TV show slug via /search?q=batman ──────────────────────
sub("Step 4.0 · Search for 'batman' to grab a live slug")

slug       = None
subject_id = None
is_series  = False

try:
    r = get("/search", params={"q": "batman"})
    assert_status(r, label="search:batman")
    results = r.json().get("movies", [])
    if not results:
        _err("search:batman returned 0 results — cannot continue deep-link chain")
    else:
        # Prefer a TV series/anime entry if present (has episodes)
        # Fallback to first result (movie)
        series_hit = next(
            (m for m in results
             if m.get("slug") and
             any(kw in (m.get("name") or "").lower()
                 for kw in ("series", "season", "animated", "show"))),
            None
        )
        chosen = series_hit or results[0]
        slug = chosen.get("slug")
        is_series = chosen is series_hit
        print(f"    chosen: name={str(chosen.get('name','?'))[:50]!r}")
        print(f"    slug  : {slug!r}  |  is_series_hint={is_series}")
        if slug:
            _ok(f"slug extracted: {slug!r}")
        else:
            _err("chosen result has no slug")
except Exception as e:
    _err(f"search:batman => {e}")

# ── Step 4.1: /detail/{slug} → extract subjectId ────────────────────────────
if slug:
    sub(f"Step 4.1 · GET /detail/{slug}")
    try:
        r = get(f"/detail/{slug}")
        assert_status(r, label=f"detail:{slug}")
        data = r.json()
        assert_keys(data, ["slug", "metadata", "streams"], f"detail:{slug}")

        meta = data.get("metadata", {})
        subject_id = meta.get("id")

        print(f"    title      : {str(meta.get('title','?'))[:50]!r}")
        print(f"    subjectId  : {subject_id}")
        print(f"    imdb       : {meta.get('imdb_rating','?')}")
        print(f"    duration   : {meta.get('duration','?')}")
        print(f"    genre      : {meta.get('genre','?')}")
        print(f"    mp4_count  : {len(data.get('streams',{}).get('mp4',[]))}")
        print(f"    hls_count  : {len(data.get('streams',{}).get('hls',[]))}")

        if subject_id:
            _ok(f"subjectId extracted: {subject_id}")
        else:
            _warn("subjectId is None — stream test will be skipped")
    except Exception as e:
        _err(f"detail:{slug} => {e}")

# ── Step 4.2: /detail/{slug}/episodes ───────────────────────────────────────
if slug:
    sub(f"Step 4.2 · GET /detail/{slug}/episodes")
    try:
        r = get(f"/detail/{slug}/episodes")
        # 404 is acceptable for movies (not a series)
        if r.status_code == 404:
            _warn(f"episodes: 404 – title appears to be a movie, not a series")
            detail_msg = r.json().get("detail", "")
            print(f"    api message: {detail_msg!r}")
        elif assert_status(r, label=f"episodes:{slug}"):
            data = r.json()
            assert_keys(data, ["slug", "total_seasons", "seasons"],
                        f"episodes:{slug}")

            total_seasons  = data.get("total_seasons", 0)
            source         = data.get("source", "?")
            print(f"    source       : {source!r}")
            print(f"    total_seasons: {total_seasons}")

            for season in data.get("seasons", []):
                ep_count = season.get("episode_count", 0)
                ep_list  = season.get("episodes", [])
                with_thumb = sum(1 for e in ep_list if e.get("thumbnail"))
                with_title = sum(1 for e in ep_list if e.get("title"))
                print(f"    Season {season.get('season'):>2} │ "
                      f"{ep_count:>3} eps │ "
                      f"titles {with_title}/{ep_count} │ "
                      f"thumbs {with_thumb}/{ep_count}")
                if ep_list:
                    e = ep_list[0]
                    print(f"      sample ep: "
                          f"ep={e.get('episode')} "
                          f"title={str(e.get('title','?'))[:35]!r} "
                          f"thumb={'YES' if e.get('thumbnail') else 'NULL'}")

            if total_seasons > 0:
                _ok(f"episodes: {total_seasons} season(s) found via {source!r}")
            else:
                _warn("episodes: endpoint returned 0 seasons")
    except Exception as e:
        _err(f"episodes:{slug} => {e}")

# ── Step 4.3: /api/stream/{subjectId}?detailPath={slug} ─────────────────────
if slug and subject_id:
    sub(f"Step 4.3 · GET /api/stream/{subject_id}?detailPath={slug}")
    try:
        r = get(
            f"/api/stream/{subject_id}",
            params={"detail_path": slug, "se": 0, "ep": 0},
        )
        assert_status(r, label=f"stream:{subject_id}")
        data = r.json()

        assert_keys(
            data,
            ["subject_id", "sources", "subtitles", "stream_domain", "uuid_used"],
            f"stream:{subject_id}",
        )

        sources   = data.get("sources", [])
        subtitles = data.get("subtitles", [])
        domain    = data.get("stream_domain", "?")
        uuid_used = data.get("uuid_used", "?")

        print(f"    stream_domain : {domain!r}")
        print(f"    uuid_used     : {uuid_used!r}")
        print(f"    source_count  : {len(sources)}")

        for src in sources:
            size_mb = (
                f"{src['size_bytes'] / 1_048_576:.1f} MB"
                if src.get("size_bytes")
                else "size N/A"
            )
            print(f"      {src.get('resolution','?'):>6}  "
                  f"{src.get('format','?'):<6}  "
                  f"{size_mb}  "
                  f"{DIM}{str(src.get('url',''))[:60]}…{RESET}")

        if sources:
            _ok(f"stream: {len(sources)} source(s) returned")
        else:
            _warn("stream: 0 sources returned")

        # Subtitles (FEATURE 5)
        print(f"    subtitle_count: {len(subtitles)}")
        if subtitles:
            for sub_track in subtitles:
                print(f"      lang={sub_track.get('language','?'):<8}  "
                      f"fmt={sub_track.get('format','?'):<4}  "
                      f"label={str(sub_track.get('label','?'))[:20]!r}  "
                      f"{DIM}{str(sub_track.get('url',''))[:55]}…{RESET}")
            _ok(f"subtitles: {len(subtitles)} track(s) found")
        else:
            _warn("subtitles: none returned (title may have no subtitle tracks)")

    except Exception as e:
        _err(f"stream:{subject_id} => {e}")
elif not subject_id:
    print(f"\n  {WARN} Stream test skipped — subjectId could not be extracted")


# ─────────────────────────────────────────────────────────────────────────────
#  SECTION 5 – Cache sanity check (two rapid-fire identical requests)
# ─────────────────────────────────────────────────────────────────────────────
section("5 · CACHE SANITY  (two identical requests, second should be faster)")

import time

for path in ["/home", "/ranking"]:
    sub(path)
    try:
        t0 = time.perf_counter()
        r1 = get(path)
        t1 = time.perf_counter() - t0

        t0 = time.perf_counter()
        r2 = get(path)
        t2 = time.perf_counter() - t0

        print(f"    request 1: {t1*1000:6.0f} ms  HTTP {r1.status_code}")
        print(f"    request 2: {t2*1000:6.0f} ms  HTTP {r2.status_code}")

        if r2.status_code == 200 and r1.status_code == 200:
            _ok(f"{path}: both requests succeeded")
        if t2 < t1:
            _ok(f"{path}: request 2 faster ({t2*1000:.0f}ms < {t1*1000:.0f}ms) — cache likely active")
        else:
            _warn(f"{path}: request 2 not faster — cache may not be active yet "
                  f"({t2*1000:.0f}ms vs {t1*1000:.0f}ms)")
    except Exception as e:
        _err(f"{path} cache check => {e}")


# ─────────────────────────────────────────────────────────────────────────────
#  Summary
# ─────────────────────────────────────────────────────────────────────────────
print(f"\n{'═'*60}")
print(f"{BOLD}  RESULTS{RESET}")
print(f"{'═'*60}")
print(f"  {PASS} Passed : {_passed}")
print(f"  {WARN} Warned : {_warned}")
print(f"  {FAIL} Failed : {_failed}")
print(f"{'═'*60}\n")

sys.exit(1 if _failed else 0)
