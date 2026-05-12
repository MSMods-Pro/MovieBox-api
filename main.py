import re
import json
import time
import uuid as uuid_lib
import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from bs4 import BeautifulSoup
from typing import Any, Optional

# ─────────────────────────────────────────────
#  App setup
# ─────────────────────────────────────────────
app = FastAPI(
    title="MovieBox API",
    description=(
        "Live REST API for moviebox.ph — scrapes all homepage sections "
        "with real poster URLs, badges, genres and more"
    ),
    version="4.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_URL = "https://moviebox.ph"

# ─────────────────────────────────────────────
#  FIX 1 ── Global Vercel-safe timeout (8 s)
#           Vercel kills at 10 s; we abort at 8 s
#           so we can handle the exception cleanly.
# ─────────────────────────────────────────────
TIMEOUT = 8.0

# ─────────────────────────────────────────────
#  FIX 2 ── Single strong HEADERS dict used
#           EVERYWHERE. No more weak inline dicts.
#
#  HEADERS      → HTML page fetches (moviebox.ph)
#  API_HEADERS  → JSON API calls (h5-api backend)
# ─────────────────────────────────────────────
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;"
        "q=0.9,image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Referer": "https://moviebox.ph/",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Upgrade-Insecure-Requests": "1",
}

API_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Content-Type": "application/json",
    "Origin": "https://moviebox.ph",
    "Referer": "https://moviebox.ph/",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "cross-site",
    "X-Client-Type": "h5",
    "X-App-Version": "1.0.0",
}


# ─────────────────────────────────────────────
#  Lightweight In-Memory TTL Cache (15 min)
# ─────────────────────────────────────────────
_CACHE: dict[str, tuple[Any, float]] = {}
CACHE_TTL = 900  # seconds


def cache_get(key: str) -> Optional[Any]:
    entry = _CACHE.get(key)
    if entry is None:
        return None
    value, ts = entry
    if time.monotonic() - ts < CACHE_TTL:
        return value
    del _CACHE[key]
    return None


def cache_set(key: str, value: Any) -> None:
    _CACHE[key] = (value, time.monotonic())


def cache_clear(key: str) -> None:
    _CACHE.pop(key, None)


# ─────────────────────────────────────────────
#  Dynamic UUID / Cookie Helper
# ─────────────────────────────────────────────
_uuid_store: dict[str, str] = {}


async def get_player_uuid() -> str:
    """
    Returns a valid UUID for the stream player cookie.
    Strategy (in order):
      1. Reuse an in-process UUID < 30 min old.
      2. Try to scrape a real session UUID from the domain endpoint.
      3. Fall back to a freshly generated UUID4.
    """
    now = time.monotonic()
    age = now - float(_uuid_store.get("fetched_at", 0))

    if _uuid_store.get("value") and age < 1800:
        return _uuid_store["value"]

    domain_url = "https://h5-api.aoneroom.com/wefeed-h5api-bff/media-player/get-domain"
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=TIMEOUT) as client:
            r = await client.get(domain_url, headers=API_HEADERS)
            if r.status_code == 200:
                set_cookie = r.headers.get("set-cookie", "")
                m = re.search(r"uuid=([0-9a-f\-]{36})", set_cookie)
                if m:
                    fresh = m.group(1)
                    _uuid_store["value"] = fresh
                    _uuid_store["fetched_at"] = str(now)
                    return fresh
    except Exception:
        pass

    fresh = str(uuid_lib.uuid4())
    _uuid_store["value"] = fresh
    _uuid_store["fetched_at"] = str(now)
    return fresh


# ─────────────────────────────────────────────
#  Scraping helpers  (all use TIMEOUT + HEADERS)
# ─────────────────────────────────────────────
async def fetch_tab(path: str) -> tuple[BeautifulSoup, str]:
    """Fetch an HTML page with strong headers and global timeout."""
    url = BASE_URL + path if path.startswith("/") else path
    req_headers = {**HEADERS, "Referer": url}
    async with httpx.AsyncClient(follow_redirects=True, timeout=TIMEOUT) as client:
        response = await client.get(url, headers=req_headers)
        if response.status_code != 200:
            raise HTTPException(
                status_code=502,
                detail=f"Failed to fetch {url}: HTTP {response.status_code}",
            )
        return BeautifulSoup(response.text, "html.parser"), response.text


def build_blurhash_to_poster_map(raw_html: str) -> dict[str, str]:
    script_match = re.search(
        r'<script[^>]+id="__NUXT_DATA__"[^>]*>(.*?)</script>',
        raw_html, re.DOTALL,
    )
    if not script_match:
        return {}
    try:
        data = json.loads(script_match.group(1))
    except Exception:
        return {}
    if not isinstance(data, list):
        return {}

    mapping: dict[str, str] = {}
    cdn_indices = [
        i for i, v in enumerate(data)
        if isinstance(v, str) and "pbcdnw.aoneroom.com" in v
    ]
    for idx in cdn_indices:
        url = data[idx]
        for offset in range(1, 12):
            for direction in (1, -1):
                neighbor_idx = idx + (direction * offset)
                if 0 <= neighbor_idx < len(data):
                    candidate = data[neighbor_idx]
                    if (
                        isinstance(candidate, str)
                        and 8 < len(candidate) < 90
                        and re.match(
                            r'^[A-Za-z0-9$%^&*:;,|{}\[\]~\-=+.@#!?_()/<>\'` ]+$',
                            candidate,
                        )
                        and not candidate.startswith("http")
                        and not candidate.startswith("/")
                        and "." not in candidate[:4]
                        and candidate not in mapping
                    ):
                        mapping[candidate] = url
                        break
    return mapping


def build_slug_to_poster_map(raw_html: str) -> dict[str, str]:
    script_match = re.search(
        r'<script[^>]+id="__NUXT_DATA__"[^>]*>(.*?)</script>',
        raw_html, re.DOTALL,
    )
    if not script_match:
        return {}
    try:
        data = json.loads(script_match.group(1))
    except Exception:
        return {}
    if not isinstance(data, list):
        return {}

    mapping: dict[str, str] = {}
    slug_re = re.compile(r"^[a-z][a-z0-9\-]+-[a-zA-Z0-9]{11}$")
    for idx, v in enumerate(data):
        if not isinstance(v, str) or not slug_re.match(v) or "-" not in v:
            continue
        if v in mapping:
            continue
        for offset in range(1, 26):
            ni = idx - offset
            if ni < 0:
                break
            candidate = data[ni]
            if isinstance(candidate, str) and "pbcdnw.aoneroom.com" in candidate:
                mapping[v] = candidate
                break
    return mapping


def parse_movie_card(card, blurhash_map: dict) -> dict:
    href = card.get("href", "")
    title_attr = card.get("title", "")
    name_tag = card.find("p")
    if name_tag:
        name = name_tag.get_text(separator=" ", strip=True)
    elif title_attr:
        name = re.sub(r"^go to\s+", "", title_attr, flags=re.IGNORECASE)
        name = re.sub(r"\s+detail page$", "", name, flags=re.IGNORECASE)
    else:
        name = ""

    slug = href.split("/detail/")[-1] if "/detail/" in href else None
    thumb_span = card.find("span", attrs={"thumbnail": True})
    blurhash = thumb_span.get("thumbnail") if thumb_span else None
    poster_url = blurhash_map.get(blurhash) if blurhash else None
    badge_span = card.find(
        "span", class_=lambda c: c and "text-white" in c if c else False
    )
    badge = badge_span.get_text(strip=True) if badge_span else None
    return {
        "name": name,
        "url": BASE_URL + href if href.startswith("/") else href,
        "slug": slug,
        "poster_url": poster_url,
        "badge": badge,
        "blurhash": blurhash,
    }


def parse_sections(soup: BeautifulSoup, blurhash_map: dict) -> list[dict]:
    sections = []
    seen_titles: set = set()
    for box in soup.find_all("div", class_="movie-card-list-box"):
        title_div = box.find(
            "div", class_=lambda c: c and "title" in c.split() if c else False
        )
        if not title_div:
            continue
        raw_title = title_div.get_text(strip=True)
        if not raw_title or raw_title in seen_titles:
            continue
        seen_titles.add(raw_title)
        more_link = box.find(
            "a", class_=lambda c: c and "action-bar" in c.split() if c else False
        )
        more_href = more_link.get("href") if more_link else None
        more_url = (
            (BASE_URL + more_href if more_href.startswith("/") else more_href)
            if more_href else None
        )
        movie_cards = box.find_all("a", class_="movie-card")
        if not movie_cards:
            continue
        movies = [parse_movie_card(card, blurhash_map) for card in movie_cards]
        sections.append({
            "section": raw_title,
            "more_url": more_url,
            "count": len(movies),
            "movies": movies,
        })
    return sections


def parse_card_page(soup: BeautifulSoup, slug_map: dict) -> list[dict]:
    cards = soup.find_all(
        "a", class_="card", href=lambda h: h and "/detail/" in h if h else False
    )
    movies = []
    for card in cards:
        href = card.get("href", "")
        slug = href.split("/detail/")[-1] if "/detail/" in href else None
        h2 = card.find(
            "h2", class_=lambda c: c and "card-title" in c.split() if c else False
        )
        name = h2.get_text(strip=True) if h2 else (slug or "")
        year_div = card.find(
            "div",
            class_=lambda c: c and "text-white" in c and "text-[12px]" in c if c else False,
        )
        year = year_div.get_text(strip=True) if year_div else None
        rating_span = card.find("span")
        rating = rating_span.get_text(strip=True) if rating_span else None
        movies.append({
            "name": name,
            "url": BASE_URL + href if href.startswith("/") else href,
            "slug": slug,
            "poster_url": slug_map.get(slug) if slug else None,
            "year": year,
            "rating": rating,
            "badge": None,
            "blurhash": None,
        })
    if not movies:
        return []
    return [{"section": "All", "more_url": None, "count": len(movies), "movies": movies}]


def parse_movie_filter_page(
    soup: BeautifulSoup, blurhash_map: dict, slug_map: dict, raw_html: str
) -> list[dict]:
    all_cards = soup.find_all(
        "a", href=lambda h: h and "/detail/" in h if h else False
    )
    movies = []
    seen_slugs: set = set()
    for card in all_cards:
        href = card.get("href", "")
        slug = href.split("/detail/")[-1] if "/detail/" in href else None
        if not slug or slug in seen_slugs:
            continue
        seen_slugs.add(slug)
        name_tag = card.find("p") or card.find(
            "h2", class_=lambda c: c and "card-title" in c if c else False
        )
        name = name_tag.get_text(strip=True) if name_tag else slug
        thumb = card.find("span", attrs={"thumbnail": True})
        blurhash = thumb.get("thumbnail") if thumb else None
        badge_span = card.find(
            "span", class_=lambda c: c and "text-white" in c if c else False
        )
        badge = badge_span.get_text(strip=True) if badge_span else None
        movies.append({
            "name": name,
            "url": BASE_URL + href if href.startswith("/") else href,
            "slug": slug,
            "poster_url": blurhash_map.get(blurhash) if blurhash else None,
            "badge": badge,
            "blurhash": blurhash,
        })

    if not movies:
        script_match = re.search(
            r'<script[^>]+id="__NUXT_DATA__"[^>]*>(.*?)</script>',
            raw_html, re.DOTALL,
        )
        if script_match:
            try:
                data = json.loads(script_match.group(1))
                slugs_idx = [
                    (i, v) for i, v in enumerate(data)
                    if isinstance(v, str)
                    and "/detail/" not in v
                    and re.match(r"^[a-z0-9][a-z0-9\-]{3,}-[a-zA-Z0-9]{11}$", v)
                ]
                for idx, slug in slugs_idx:
                    if slug in seen_slugs:
                        continue
                    seen_slugs.add(slug)
                    name = slug
                    for j in range(max(0, idx - 10), min(len(data), idx + 10)):
                        val = data[j]
                        if (
                            isinstance(val, str) and len(val) > 2
                            and not val.startswith("http")
                            and not re.match(r"^[a-z0-9\-]+$", val)
                        ):
                            name = val.replace("Trailer-", "").strip()
                            break
                    movies.append({
                        "name": name,
                        "url": BASE_URL + f"/detail/{slug}",
                        "slug": slug,
                        "poster_url": slug_map.get(slug),
                        "badge": None,
                        "blurhash": None,
                    })
            except Exception:
                pass

    if not movies:
        return []
    return [{
        "section": "All Movies",
        "more_url": None,
        "count": len(movies),
        "movies": movies,
    }]


def _resolve_nuxt_data(data, index):
    if not isinstance(index, int) or index < 0 or index >= len(data):
        return index
    val = data[index]
    if isinstance(val, dict):
        return {k: _resolve_nuxt_data(data, v) for k, v in val.items()}
    elif isinstance(val, list):
        return [_resolve_nuxt_data(data, i) for i in val]
    return val


def parse_ranking_page(soup: BeautifulSoup, slug_map: dict) -> list[dict]:
    rank_lists = soup.find_all(
        "div", class_=lambda c: c and "rank-subject-list" in c if c else False
    )
    all_movies = []
    seen_slugs: set = set()
    for rl in rank_lists:
        cards = rl.find_all(
            "a", class_=lambda c: c and "rank-subject-item" in c if c else False
        )
        for card in cards:
            href = card.get("href", "")
            slug = href.split("/detail/")[-1] if "/detail/" in href else None
            if slug in seen_slugs:
                continue
            seen_slugs.add(slug)
            title_div = card.find("div", class_="title-text")
            name = title_div.get_text(strip=True) if title_div else (slug or "")
            rank_num_div = card.find("div", class_="ranking-corner-num")
            rank = rank_num_div.get_text(strip=True) if rank_num_div else None
            badge_span = card.find("span", class_="special-tag-text")
            badge = badge_span.get_text(strip=True) if badge_span else None
            all_movies.append({
                "name": name,
                "url": BASE_URL + href if href.startswith("/") else href,
                "slug": slug,
                "rank": rank,
                "poster_url": slug_map.get(slug) if slug else None,
                "badge": badge,
            })
    if not all_movies:
        return []
    return [{
        "section": "Most Watched",
        "more_url": None,
        "count": len(all_movies),
        "movies": all_movies,
    }]


# ─────────────────────────────────────────────
#  Shared helpers for category endpoints
# ─────────────────────────────────────────────
async def _tab_sections(path: str) -> tuple[list, int]:
    """HTML scrape → parse.  Raises on network failure (caller handles it)."""
    soup, raw = await fetch_tab(path)
    bmap = build_blurhash_to_poster_map(raw)
    smap = build_slug_to_poster_map(raw)

    if (
        soup.find("a", class_="card", href=lambda h: h and "/detail/" in h if h else False)
        or "tv-series" in path
        or "animation" in path
    ):
        sections = parse_card_page(soup, smap)
    elif (
        soup.find("a", class_=lambda c: c and "rank-subject-item" in c if c else False)
        or "ranking" in path
    ):
        sections = parse_ranking_page(soup, smap)
    elif (
        soup.find("div", class_=lambda c: c and "filter-name" in " ".join(c) if c else False)
        or "movie" in path
    ):
        sections = parse_movie_filter_page(soup, bmap, smap, raw)
    else:
        sections = parse_sections(soup, bmap)

    return sections, len(bmap)


async def _paginated_api(category: str, page: int, per_page: int) -> dict:
    """
    Hit h5-api subject/list with strong headers and TIMEOUT.
    Returns {} on any failure — callers must check for empty dict.
    """
    cat_id_map = {"movie": 1, "tv-series": 2, "animated-series": 3}
    cat_id = cat_id_map.get(category, 1)
    url = (
        f"https://h5-api.aoneroom.com/wefeed-h5api-bff/subject/list"
        f"?categoryId={cat_id}&page={page}&perPage={per_page}&host=moviebox.ph"
    )
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=TIMEOUT) as client:
            resp = await client.get(url, headers=API_HEADERS)
        if resp.status_code != 200:
            return {}
        data = resp.json().get("data", {})
        items = data.get("items") or data.get("subjects") or []
        total = data.get("total") or data.get("totalCount") or len(items)
        movies = []
        for sub in items:
            detail_path = sub.get("detailPath")
            movies.append({
                "name": sub.get("title") or sub.get("name"),
                "poster_url": sub.get("cover", {}).get("url") or sub.get("thumbnail"),
                "url": BASE_URL + f"/detail/{detail_path}" if detail_path else None,
                "slug": detail_path,
                "badge": sub.get("corner"),
                "blurhash": sub.get("cover", {}).get("blurHash"),
            })
        return {
            "page": page, "per_page": per_page,
            "total": total, "has_more": page * per_page < total,
            "movies": movies,
        }
    except Exception:
        return {}


def _empty_paged(page: int, limit: int, section_label: str) -> dict:
    """
    FIX 3 ── Valid empty response returned when BOTH upstreams fail.
    The frontend receives a predictable shape instead of a 502 crash.
    """
    return {
        "source": "unavailable",
        "page": page, "per_page": limit,
        "total": 0, "has_more": False,
        "total_sections": 0,
        "poster_map_size": 0,
        "sections": [{
            "section": section_label,
            "more_url": None,
            "count": 0,
            "movies": [],
        }],
    }


# ─────────────────────────────────────────────
#  Root
# ─────────────────────────────────────────────
@app.get("/")
def list_endpoints():
    return {
        "api": "MovieBox API",
        "version": "4.0.0",
        "docs": "/docs",
        "endpoints": {
            "home": {
                "/home": "Full homepage data (cached, 15 min TTL)",
                "/home/sections": "Section names and counts",
                "/home/banner": "Featured banner items",
                "/home/trending": "Trending Now section",
                "/home/hot": "Hot section",
                "/home/cinema": "Cinema section",
                "/home/section/{name}": "Any section by partial name",
            },
            "movies": {
                "/movies": "Movie catalog (supports ?page=&limit=)",
                "/movies/sections": "List movie sections",
                "/movies/section/{name}": "Movie section by name",
            },
            "tv_series": {
                "/tv-series": "TV series catalog (supports ?page=&limit=)",
                "/tv-series/sections": "List TV series sections",
                "/tv-series/section/{name}": "TV section by name",
            },
            "animation": {
                "/animation": "Animation catalog (supports ?page=&limit=)",
                "/animation/sections": "List animation sections",
                "/animation/section/{name}": "Animation section by name",
            },
            "ranking": {
                "/ranking": "Ranking lists (cached)",
                "/ranking/sections": "List ranking sections",
                "/ranking/section/{name}": "Ranking section by name",
            },
            "search": {
                "/search?q=": "Full-text search",
                "/search/suggest?q=": "Autocomplete suggestions",
            },
            "detail": {
                "/detail/{slug}": "Full metadata + basic streams",
                "/detail/{slug}/episodes": "All seasons & episodes (TV / Anime)",
            },
            "streams": {
                "/api/stream/{subject_id}": "Stream sources + subtitles",
            },
        },
    }


# ─────────────────────────────────────────────
#  /home  — FIX 1 + 2 + 3
# ─────────────────────────────────────────────
@app.get("/home")
async def get_home():
    cached = cache_get("home")
    if cached is not None:
        return cached

    h5_url = "https://h5-api.aoneroom.com/wefeed-h5api-bff/home?host=moviebox.ph"
    sections: list[dict] = []

    # FIX 3 ── wrap everything; never let a timeout bubble up as 500
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=TIMEOUT) as client:
            resp = await client.get(h5_url, headers=API_HEADERS)  # FIX 2

        if resp.status_code == 200:
            data = resp.json().get("data", {})
            for op in data.get("operatingList", []):
                title = op.get("title", "")

                if op.get("banner"):
                    banner_items = []
                    for item in op["banner"].get("items", []):
                        name = item.get("title", "")
                        if not name or "Communities" in name:
                            continue
                        poster = item.get("image", {}).get("url")
                        if not poster and item.get("subject"):
                            poster = item["subject"].get("cover", {}).get("url")
                        detail_path = item.get("detailPath")
                        badge = (
                            item["subject"].get("corner")
                            if item.get("subject") else None
                        )
                        banner_items.append({
                            "name": name,
                            "poster_url": poster,
                            "url": BASE_URL + f"/detail/{detail_path}"
                            if detail_path else None,
                            "badge": badge,
                            "slug": detail_path,
                        })
                    sections.append({
                        "section": "Banner",
                        "count": len(banner_items),
                        "movies": banner_items,
                        "more_url": None,
                    })
                    continue

                subs = op.get("subjects", [])
                if not subs or not title:
                    continue
                movies = []
                for sub in subs:
                    detail_path = sub.get("detailPath")
                    movies.append({
                        "name": sub.get("title") or sub.get("name"),
                        "poster_url": sub.get("cover", {}).get("url") or sub.get("thumbnail"),
                        "url": BASE_URL + f"/detail/{detail_path}" if detail_path else None,
                        "slug": detail_path,
                        "badge": sub.get("corner"),
                        "blurhash": sub.get("cover", {}).get("blurHash"),
                    })
                sections.append({
                    "section": title,
                    "count": len(movies),
                    "movies": movies,
                    "more_url": None,
                })

    except (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.TimeoutException):
        pass  # return whatever sections we collected (may be [])
    except Exception:
        pass

    result = {
        "source": h5_url,
        "total_sections": len(sections),
        "sections": sections,
    }
    if sections:  # only cache a non-empty response
        cache_set("home", result)
    return result


@app.get("/home/sections")
async def get_section_names():
    home_data = await get_home()
    return {
        "total": len(home_data["sections"]),
        "sections": [
            {"name": s["section"], "count": s["count"], "more_url": s.get("more_url")}
            for s in home_data["sections"]
        ],
    }


@app.get("/home/banner")
async def get_banner():
    home_data = await get_home()
    for s in home_data["sections"]:
        if s["section"] == "Banner":
            return {"count": s["count"], "featured": s["movies"]}
    return {"count": 0, "featured": []}


@app.get("/home/trending")
async def get_trending():
    home_data = await get_home()
    for s in home_data["sections"]:
        if "trending now" in s["section"].lower():
            return s
    # FIX 3 ── graceful empty instead of 404 crash
    return {"section": "Trending Now", "count": 0, "movies": [], "more_url": None}


@app.get("/home/hot")
async def get_hot():
    home_data = await get_home()
    for s in home_data["sections"]:
        if "hot" in s["section"].lower():
            return s
    return {"section": "Hot", "count": 0, "movies": [], "more_url": None}


@app.get("/home/cinema")
async def get_cinema():
    home_data = await get_home()
    for s in home_data["sections"]:
        if "cinema" in s["section"].lower():
            return s
    return {"section": "Cinema", "count": 0, "movies": [], "more_url": None}


@app.get("/home/section/{name}")
async def get_section_by_name(name: str):
    home_data = await get_home()
    sections = home_data["sections"]
    matched = [s for s in sections if name.lower() in s["section"].lower()]
    # FIX 3 ── return empty result with hint instead of raising 404
    if not matched:
        return {
            "results": [],
            "message": f"No section matching '{name}'",
            "available": [s["section"] for s in sections],
        }
    return {"results": matched}


# ─────────────────────────────────────────────
#  /detail/{slug}  — FIX 1 + 2 + 3
# ─────────────────────────────────────────────
@app.get("/detail/{slug}")
async def get_movie_detail(slug: str):
    url = f"https://moviebox.ph/detail/{slug}"
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=TIMEOUT) as client:
            resp = await client.get(url, headers=HEADERS)  # FIX 2

        if resp.status_code != 200:
            return {
                "slug": slug,
                "error": f"Upstream returned HTTP {resp.status_code}",
                "metadata": {}, "streams": {},
            }

        match = re.search(
            r'<script type="application/json" data-nuxt-data="nuxt-app" '
            r'data-ssr="true" id="__NUXT_DATA__">\s*(.*?)\s*</script>',
            resp.text, re.DOTALL,
        )
        if not match:
            return {"slug": slug, "error": "NUXT data not found", "metadata": {}, "streams": {}}

        nuxt_json = json.loads(match.group(1))
        if not isinstance(nuxt_json, list):
            return {"slug": slug, "error": "Unexpected NUXT format", "metadata": {}, "streams": {}}

        movie_dict = None
        for i, v in enumerate(nuxt_json):
            if isinstance(v, dict) and "subjectId" in v and "title" in v and "duration" in v:
                movie_dict = _resolve_nuxt_data(nuxt_json, i)
                break

        if not movie_dict:
            return {
                "slug": slug,
                "error": "Metadata not found in NUXT",
                "metadata": {}, "streams": {},
            }

        stream_urls = [s for s in nuxt_json if isinstance(s, str) and ".mp4" in s]
        hls_urls = [
            s for s in nuxt_json
            if isinstance(s, str) and (".m3u8" in s or "/m3u8/" in s)
        ]

        return {
            "slug": slug,
            "source": url,
            "metadata": {
                "id": movie_dict.get("subjectId"),
                "title": movie_dict.get("title"),
                "description": movie_dict.get("description"),
                "release_date": movie_dict.get("releaseDate"),
                "duration": movie_dict.get("duration"),
                "genre": movie_dict.get("genre"),
                "country": movie_dict.get("countryName"),
                "imdb_rating": movie_dict.get("imdbRatingValue"),
                "poster": movie_dict.get("cover", {}).get("url")
                if isinstance(movie_dict.get("cover"), dict) else None,
                "badge": movie_dict.get("corner"),
                "dubs": movie_dict.get("dubs", []),
            },
            "streams": {"mp4": stream_urls, "hls": hls_urls},
        }

    except (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.TimeoutException):
        return {"slug": slug, "error": "Request timed out", "metadata": {}, "streams": {}}
    except Exception as e:
        return {"slug": slug, "error": str(e), "metadata": {}, "streams": {}}


# ─────────────────────────────────────────────
#  /detail/{slug}/episodes  — FIX 1 + 2 + 3 + 4
# ─────────────────────────────────────────────
@app.get("/detail/{slug}/episodes")
async def get_episodes(slug: str):
    # ── Strategy A: dedicated h5-api episodes endpoint ───────────────────────
    api_url = (
        "https://h5-api.aoneroom.com/wefeed-h5api-bff/subject/episode-list"
        f"?detailPath={slug}&page=1&perPage=200"
    )
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=TIMEOUT) as client:
            r = await client.get(api_url, headers=API_HEADERS)  # FIX 2
        if r.status_code == 200:
            ep_data = r.json().get("data", {})
            items = ep_data.get("items") or ep_data.get("episodes", [])
            season_map: dict[int, list] = {}
            for ep in items:
                se_num = ep.get("seasonNo") or ep.get("season") or 1
                season_map.setdefault(se_num, []).append({
                    "episode": ep.get("episodeNo") or ep.get("episode"),
                    "title": ep.get("title") or ep.get("name"),
                    "duration": ep.get("duration"),
                    "thumbnail": (ep.get("cover") or {}).get("url") or ep.get("thumbnail"),
                    "slug": ep.get("detailPath"),
                    "url": BASE_URL + f"/detail/{ep['detailPath']}"
                    if ep.get("detailPath") else None,
                })
            if season_map:
                seasons_result = [
                    {"season": se, "episode_count": len(eps), "episodes": eps}
                    for se, eps in sorted(season_map.items())
                ]
                return {
                    "slug": slug,
                    "source": "h5-api",
                    "total_seasons": len(seasons_result),
                    "seasons": seasons_result,
                }
    except (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.TimeoutException):
        pass  # fall through to NUXT parse
    except Exception:
        pass

    # ── Strategy B: NUXT parse from detail page ───────────────────────────────
    try:
        page_url = f"https://moviebox.ph/detail/{slug}"
        async with httpx.AsyncClient(follow_redirects=True, timeout=TIMEOUT) as client:
            resp = await client.get(page_url, headers=HEADERS)  # FIX 2

        if resp.status_code != 200:
            # FIX 3 ── empty JSON, not an exception
            return {
                "slug": slug, "error": "Detail page unavailable",
                "total_seasons": 0, "seasons": [],
            }

        match = re.search(
            r'<script[^>]+id="__NUXT_DATA__"[^>]*>(.*?)</script>',
            resp.text, re.DOTALL,
        )
        if not match:
            return {
                "slug": slug, "error": "NUXT data not found",
                "total_seasons": 0, "seasons": [],
            }

        nuxt = json.loads(match.group(1))
        season_map_nuxt: dict[int, list] = {}
        for i, v in enumerate(nuxt):
            if not isinstance(v, dict):
                continue
            if not (
                "episodeNo" in v or "episode" in v
                or "seasonNo" in v or "season" in v
            ):
                continue
            resolved = _resolve_nuxt_data(nuxt, i)
            se_num = resolved.get("seasonNo") or resolved.get("season") or 1
            ep_entry = {
                "episode": resolved.get("episodeNo") or resolved.get("episode"),
                "title": resolved.get("title") or resolved.get("name"),
                "duration": resolved.get("duration"),
                "thumbnail": (resolved.get("cover") or {}).get("url")
                or resolved.get("thumbnail"),
                "slug": resolved.get("detailPath"),
                "url": BASE_URL + f"/detail/{resolved['detailPath']}"
                if resolved.get("detailPath") else None,
            }
            season_map_nuxt.setdefault(se_num, []).append(ep_entry)

        # Deduplicate
        for se_num, eps in season_map_nuxt.items():
            seen: set = set()
            unique = []
            for ep in eps:
                key = (ep["episode"], ep["slug"])
                if key not in seen:
                    seen.add(key)
                    unique.append(ep)
            season_map_nuxt[se_num] = unique

        if not season_map_nuxt:
            return {
                "slug": slug,
                "message": "No episode data found — title may be a movie, not a series.",
                "total_seasons": 0, "seasons": [],
            }

        seasons_result = [
            {"season": se, "episode_count": len(eps), "episodes": eps}
            for se, eps in sorted(season_map_nuxt.items())
        ]
        return {
            "slug": slug,
            "source": "nuxt-parse",
            "total_seasons": len(seasons_result),
            "seasons": seasons_result,
        }

    except (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.TimeoutException):
        return {"slug": slug, "error": "Request timed out", "total_seasons": 0, "seasons": []}
    except Exception as e:
        return {"slug": slug, "error": str(e), "total_seasons": 0, "seasons": []}


# ─────────────────────────────────────────────
#  /movies  — FIX 1 + 2 + 3
# ─────────────────────────────────────────────
@app.get("/movies")
async def get_movies(
    page: int = Query(1, ge=1, description="Page number (1-based)"),
    limit: int = Query(30, ge=1, le=100, description="Items per page"),
):
    cache_key = f"movies:p{page}:l{limit}"
    cached = cache_get(cache_key)
    if cached is not None:
        return cached

    # Page 1: HTML scraper → richer badge/poster data
    if page == 1 and limit >= 20:
        try:
            sections, map_size = await _tab_sections("/web/movie")
            result = {
                "source": BASE_URL + "/web/movie",
                "page": 1, "per_page": limit,
                "total_sections": len(sections),
                "poster_map_size": map_size,
                "sections": sections,
            }
            cache_set(cache_key, result)
            return result
        except Exception:
            pass  # fall through to h5-api

    # h5-api pagination path
    paged = await _paginated_api("movie", page, limit)
    if not paged:
        # FIX 3 ── valid empty structure, not a 502 crash
        return _empty_paged(page, limit, "All Movies")

    result = {
        "source": "h5-api",
        "total_sections": 1,
        "sections": [{
            "section": "All Movies",
            "more_url": None,
            "count": len(paged["movies"]),
            "movies": paged["movies"],
        }],
        **{k: paged[k] for k in ("page", "per_page", "total", "has_more")},
    }
    cache_set(cache_key, result)
    return result


@app.get("/movies/sections")
async def get_movies_sections():
    try:
        sections, _ = await _tab_sections("/web/movie")
    except Exception:
        sections = []
    return {
        "total": len(sections),
        "sections": [{"name": s["section"], "count": s["count"]} for s in sections],
    }


@app.get("/movies/section/{name}")
async def get_movies_section(name: str):
    try:
        sections, _ = await _tab_sections("/web/movie")
    except Exception:
        sections = []
    matched = [s for s in sections if name.lower() in s["section"].lower()]
    if not matched:
        return {
            "results": [],
            "message": f"No section matching '{name}'",
            "available": [s["section"] for s in sections],
        }
    return {"results": matched}


# ─────────────────────────────────────────────
#  /tv-series  — FIX 1 + 2 + 3
# ─────────────────────────────────────────────
@app.get("/tv-series")
async def get_tv_series(
    page: int = Query(1, ge=1),
    limit: int = Query(30, ge=1, le=100),
):
    cache_key = f"tv-series:p{page}:l{limit}"
    cached = cache_get(cache_key)
    if cached is not None:
        return cached

    if page == 1 and limit >= 20:
        try:
            sections, map_size = await _tab_sections("/web/tv-series")
            result = {
                "source": BASE_URL + "/web/tv-series",
                "page": 1, "per_page": limit,
                "total_sections": len(sections),
                "poster_map_size": map_size,
                "sections": sections,
            }
            cache_set(cache_key, result)
            return result
        except Exception:
            pass

    paged = await _paginated_api("tv-series", page, limit)
    if not paged:
        return _empty_paged(page, limit, "All TV Series")

    result = {
        "source": "h5-api",
        "total_sections": 1,
        "sections": [{
            "section": "All TV Series",
            "more_url": None,
            "count": len(paged["movies"]),
            "movies": paged["movies"],
        }],
        **{k: paged[k] for k in ("page", "per_page", "total", "has_more")},
    }
    cache_set(cache_key, result)
    return result


@app.get("/tv-series/sections")
async def get_tv_series_sections():
    try:
        sections, _ = await _tab_sections("/web/tv-series")
    except Exception:
        sections = []
    return {
        "total": len(sections),
        "sections": [{"name": s["section"], "count": s["count"]} for s in sections],
    }


@app.get("/tv-series/section/{name}")
async def get_tv_series_section(name: str):
    try:
        sections, _ = await _tab_sections("/web/tv-series")
    except Exception:
        sections = []
    matched = [s for s in sections if name.lower() in s["section"].lower()]
    if not matched:
        return {
            "results": [],
            "message": f"No section matching '{name}'",
            "available": [s["section"] for s in sections],
        }
    return {"results": matched}


# ─────────────────────────────────────────────
#  /animation  — FIX 1 + 2 + 3
# ─────────────────────────────────────────────
@app.get("/animation")
async def get_animation(
    page: int = Query(1, ge=1),
    limit: int = Query(30, ge=1, le=100),
):
    cache_key = f"animation:p{page}:l{limit}"
    cached = cache_get(cache_key)
    if cached is not None:
        return cached

    if page == 1 and limit >= 20:
        try:
            sections, map_size = await _tab_sections("/web/animated-series")
            result = {
                "source": BASE_URL + "/web/animated-series",
                "page": 1, "per_page": limit,
                "total_sections": len(sections),
                "poster_map_size": map_size,
                "sections": sections,
            }
            cache_set(cache_key, result)
            return result
        except Exception:
            pass

    paged = await _paginated_api("animated-series", page, limit)
    if not paged:
        return _empty_paged(page, limit, "All Animations")

    result = {
        "source": "h5-api",
        "total_sections": 1,
        "sections": [{
            "section": "All Animations",
            "more_url": None,
            "count": len(paged["movies"]),
            "movies": paged["movies"],
        }],
        **{k: paged[k] for k in ("page", "per_page", "total", "has_more")},
    }
    cache_set(cache_key, result)
    return result


@app.get("/animation/sections")
async def get_animation_sections():
    try:
        sections, _ = await _tab_sections("/web/animated-series")
    except Exception:
        sections = []
    return {
        "total": len(sections),
        "sections": [{"name": s["section"], "count": s["count"]} for s in sections],
    }


@app.get("/animation/section/{name}")
async def get_animation_section(name: str):
    try:
        sections, _ = await _tab_sections("/web/animated-series")
    except Exception:
        sections = []
    matched = [s for s in sections if name.lower() in s["section"].lower()]
    if not matched:
        return {
            "results": [],
            "message": f"No section matching '{name}'",
            "available": [s["section"] for s in sections],
        }
    return {"results": matched}


# ─────────────────────────────────────────────
#  /ranking  — FIX 1 + 2 + 3
# ─────────────────────────────────────────────
@app.get("/ranking")
async def get_ranking():
    cached = cache_get("ranking")
    if cached is not None:
        return cached

    try:
        sections, map_size = await _tab_sections("/ranking-list")
    except (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.TimeoutException):
        sections, map_size = [], 0
    except Exception:
        sections, map_size = [], 0

    result = {
        "source": BASE_URL + "/ranking-list",
        "total_sections": len(sections),
        "poster_map_size": map_size,
        "sections": sections,
    }
    if sections:
        cache_set("ranking", result)
    return result


@app.get("/ranking/sections")
async def get_ranking_sections():
    data = await get_ranking()
    return {
        "total": len(data["sections"]),
        "sections": [
            {"name": s["section"], "count": s["count"]}
            for s in data["sections"]
        ],
    }


@app.get("/ranking/section/{name}")
async def get_ranking_section(name: str):
    data = await get_ranking()
    matched = [
        s for s in data["sections"]
        if name.lower() in s["section"].lower()
    ]
    if not matched:
        return {
            "results": [],
            "message": f"No section matching '{name}'",
            "available": [s["section"] for s in data["sections"]],
        }
    return {"results": matched}


# ─────────────────────────────────────────────
#  /search  — FIX 1 + 2 + 3
# ─────────────────────────────────────────────
@app.get("/search/suggest")
async def get_search_suggestions(q: str):
    url = "https://h5-api.aoneroom.com/wefeed-h5api-bff/subject/search-suggest"
    payload = {"keyword": q, "perPage": 10}
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=TIMEOUT) as client:
            resp = await client.post(url, json=payload, headers=API_HEADERS)  # FIX 2
        if resp.status_code != 200:
            return {"query": q, "suggestions": [], "error": f"HTTP {resp.status_code}"}
        items = resp.json().get("data", {}).get("items", [])
        return {"query": q, "suggestions": [i.get("word") for i in items if i.get("word")]}
    except (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.TimeoutException):
        return {"query": q, "suggestions": [], "error": "Request timed out"}
    except Exception as e:
        return {"query": q, "suggestions": [], "error": str(e)}


@app.get("/search")
async def get_search_results(q: str):
    url = "https://h5-api.aoneroom.com/wefeed-h5api-bff/subject/search"
    payload = {"keyword": q, "perPage": 30, "page": 1}
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=TIMEOUT) as client:
            resp = await client.post(url, json=payload, headers=API_HEADERS)  # FIX 2
        if resp.status_code != 200:
            return {"query": q, "count": 0, "movies": [], "error": f"HTTP {resp.status_code}"}
        items = resp.json().get("data", {}).get("items", [])
        movies = []
        for sub in items:
            detail_path = sub.get("detailPath")
            movies.append({
                "name": sub.get("title"),
                "poster_url": sub.get("cover", {}).get("url"),
                "url": BASE_URL + f"/detail/{detail_path}" if detail_path else None,
                "slug": detail_path,
                "badge": sub.get("corner"),
                "blurhash": sub.get("cover", {}).get("blurHash"),
            })
        return {"query": q, "count": len(movies), "movies": movies}
    except (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.TimeoutException):
        return {"query": q, "count": 0, "movies": [], "error": "Request timed out"}
    except Exception as e:
        return {"query": q, "count": 0, "movies": [], "error": str(e)}


# ─────────────────────────────────────────────
#  Subtitle extractor (unchanged logic)
# ─────────────────────────────────────────────
def _extract_subtitles(raw_data: dict) -> list[dict]:
    subtitles: list[dict] = []
    sub_ext = (".vtt", ".srt")

    def _walk(obj, depth=0):
        if depth > 8:
            return
        if isinstance(obj, dict):
            for key in ("subtitles", "captions", "tracks", "textTracks", "subs"):
                if key in obj and isinstance(obj[key], list):
                    for track in obj[key]:
                        if not isinstance(track, dict):
                            continue
                        url = track.get("url") or track.get("src") or track.get("file")
                        if url and any(url.lower().endswith(ext) for ext in sub_ext):
                            subtitles.append({
                                "language": (
                                    track.get("language") or track.get("lang")
                                    or track.get("label") or "unknown"
                                ),
                                "label": track.get("label") or track.get("name"),
                                "format": "vtt" if url.lower().endswith(".vtt") else "srt",
                                "url": url,
                            })
            for v in obj.values():
                _walk(v, depth + 1)
        elif isinstance(obj, list):
            for item in obj:
                _walk(item, depth + 1)
        elif isinstance(obj, str):
            if any(obj.lower().endswith(ext) for ext in sub_ext):
                subtitles.append({
                    "language": "unknown", "label": None,
                    "format": "vtt" if obj.lower().endswith(".vtt") else "srt",
                    "url": obj,
                })

    _walk(raw_data)
    seen_urls: set = set()
    unique: list[dict] = []
    for s in subtitles:
        if s["url"] not in seen_urls:
            seen_urls.add(s["url"])
            unique.append(s)
    return unique


# ─────────────────────────────────────────────
#  /api/stream  — FIX 1 + 2 + 3 + 4
# ─────────────────────────────────────────────
@app.get("/api/stream/{subject_id}")
async def get_stream_sources(
    subject_id: str,
    detail_path: str,
    se: int = 0,
    ep: int = 0,
):
    domain = "https://123movienow.cc"
    domain_url = "https://h5-api.aoneroom.com/wefeed-h5api-bff/media-player/get-domain"

    # ── Resolve player domain with strong headers + TIMEOUT ──────────────────
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=TIMEOUT) as client:
            r_dom = await client.get(domain_url, headers=API_HEADERS)  # FIX 2
            if r_dom.status_code == 200:
                dom_val = r_dom.json().get("data", domain)
                domain = (dom_val or domain).rstrip("/")
    except Exception:
        pass  # keep fallback domain

    # ── Dynamic UUID ──────────────────────────────────────────────────────────
    player_uuid = await get_player_uuid()

    play_url = (
        f"{domain}/wefeed-h5api-bff/subject/play"
        f"?subjectId={subject_id}&se={se}&ep={ep}&detailPath={detail_path}"
    )

    # FIX 2 ── all player request headers in one place, using global UA
    play_headers = {
        "accept": "application/json",
        "accept-language": "en-US,en;q=0.9",
        "accept-encoding": "gzip, deflate, br",
        "referer": (
            f"{domain}/spa/videoPlayPage/movies/{detail_path}"
            f"?id={subject_id}&type=/movie/detail&detailSe=&detailEp=&lang=en"
        ),
        "user-agent": HEADERS["User-Agent"],
        "x-client-info": '{"timezone":"Asia/Dhaka"}',
        "x-client-type": "h5",
        "x-app-version": "1.0.0",
        "x-source": "",
        "origin": domain,
    }
    cookies = {"uuid": player_uuid}

    # FIX 3 + 4 ── catch timeout gracefully, return empty sources not 500
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=TIMEOUT) as client:
            resp = await client.get(play_url, headers=play_headers, cookies=cookies)

        if resp.status_code != 200:
            return {
                "subject_id": subject_id,
                "detail_path": detail_path,
                "error": f"Player API returned HTTP {resp.status_code}",
                "sources": [], "subtitles": [],
            }

        data = resp.json()
        streams_raw = data.get("data", {}).get("streams", [])

        if not streams_raw:
            return {
                "subject_id": subject_id,
                "detail_path": detail_path,
                "error": "No streams found — hasResource may be False",
                "sources": [], "subtitles": [],
            }

        formatted_streams = []
        for s in streams_raw:
            formatted_streams.append({
                "resolution": s.get("resolutions") + "p"
                if s.get("resolutions") else "Unknown",
                "format": s.get("format"),
                "url": s.get("url"),
                "size_bytes": s.get("size"),
                "id": s.get("id"),
            })
        try:
            formatted_streams.sort(
                key=lambda x: int(x["resolution"].replace("p", "")), reverse=True
            )
        except Exception:
            pass

        subtitles = _extract_subtitles(data.get("data", {}))

        return {
            "subject_id": subject_id,
            "detail_path": detail_path,
            "season": se,
            "episode": ep,
            "stream_domain": domain,
            "uuid_used": player_uuid,
            "count": len(formatted_streams),
            "sources": formatted_streams,
            "subtitles": subtitles,
            "raw": data.get("data", {}),
        }

    except (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.TimeoutException):
        return {
            "subject_id": subject_id,
            "detail_path": detail_path,
            "error": "Stream request timed out (Vercel 8 s budget exceeded)",
            "sources": [], "subtitles": [],
        }
    except Exception as e:
        return {
            "subject_id": subject_id,
            "detail_path": detail_path,
            "error": str(e),
            "sources": [], "subtitles": [],
        }
