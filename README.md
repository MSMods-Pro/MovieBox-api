<p align="center">
  <img src="https://h5-static.aoneroom.com/ssrStatic/mbOfficial/public/_nuxt/web-logo.apJjVir2.svg" alt="MovieBox Logo" width="200"/>
</p>

<h1 align="center">🎬 MovieBox API</h1>

<p align="center">
  <a href="https://fastapi.tiangolo.com/"><img src="https://img.shields.io/badge/FastAPI-005571?style=for-the-badge&logo=fastapi" alt="FastAPI"/></a>
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/Python-3.9+-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python"/></a>
  <img src="https://img.shields.io/badge/Version-4.0.0-ff6b6b?style=for-the-badge" alt="Version"/>
  <img src="https://img.shields.io/badge/Status-Live-4c1?style=for-the-badge" alt="Status"/>
  <a href="https://opensource.org/licenses/MIT"><img src="https://img.shields.io/badge/License-MIT-blue.svg?style=for-the-badge" alt="License"/></a>
</p>

<p align="center">
  <strong>The ultimate REST API for MovieBox.ph.</strong><br/>
  High-speed metadata scraping · Real-time stream extraction · Multi-language subtitles · Smart caching · Infinite pagination
</p>

---

## ✨ Core Features

| Feature | Description |
|---|---|
| ⚡ **Smart TTL Cache** | In-memory 15-minute cache on heavy endpoints for sub-millisecond repeat responses and upstream rate-limit protection |
| 📑 **Full Pagination** | Browse massive catalogs with `?page=` & `?limit=` on every category endpoint |
| 📺 **Seasons & Episodes** | Dedicated endpoint that extracts full structured season/episode lists for TV Series and Anime |
| 💬 **Multi-Language Subtitles** | Stream endpoint recursively discovers and returns `.vtt` and `.srt` subtitle tracks at every nesting depth |
| 🛡️ **Dynamic UUID Bypass** | Automated fresh token generation and rotation for the streaming player — no more cookie expiration or stream failures |
| 🏠 **Comprehensive Homepage** | Banners, Trending Now, Hot, Cinema, and all operating category sections in a single call |
| 🗃️ **Deep Metadata** | IMDb ratings, release dates, genres, country, dub tracks, BlurHash, and high-res CDN poster URLs |
| ▶️ **Direct Streaming** | Raw `.mp4` and HLS `.m3u8` stream discovery with automatic player-domain resolution |
| 🧪 **Advanced Test Suite** | Colorful `verify.py` CLI — deep-link chain testing, cache speed benchmarking, and live coverage of every endpoint |

---

## 🛠️ Tech Stack

| Layer | Tool |
|---|---|
| **Backend Framework** | [FastAPI](https://fastapi.tiangolo.com/) |
| **Async HTTP Client** | [httpx](https://www.python-httpx.org/) |
| **HTML Parsing** | [BeautifulSoup4](https://www.crummy.com/software/BeautifulSoup/) + Regex (NUXT_DATA extraction) |
| **Caching** | Built-in in-memory TTL dict (zero external dependencies) |
| **Server** | [Uvicorn](https://www.uvicorn.org/) |

---

## 🚀 Getting Started

### Prerequisites

- Python **3.9** or higher
- `pip` package installer

### Installation

**1. Clone the repository**
```bash
git clone https://github.com/DPModsPro/MovieBox-api.git
cd MovieBox-api
```

**2. Install dependencies**
```bash
pip install -r requirements.txt
```
> If `requirements.txt` is missing, install manually:
> ```bash
> pip install fastapi uvicorn httpx beautifulsoup4
> ```

**3. Start the server**
```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

The API will be live at `http://localhost:8000`.  
Interactive docs are available at `http://localhost:8000/docs`.

---

## 📡 API Endpoints

### 🏠 Home *(Cached — 15 min TTL)*

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/home` | Full homepage data — banners, trending, hot, and all operating sections |
| `GET` | `/home/banner` | Featured banner items with poster URLs and badges |
| `GET` | `/home/sections` | List of all section names and their movie counts |
| `GET` | `/home/trending` | "Trending Now" section only |
| `GET` | `/home/hot` | "Hot" section only |
| `GET` | `/home/cinema` | "In Cinemas" section only |
| `GET` | `/home/section/{name}` | Any section by partial name match |

---

### 🎬 Movies, TV & Animation *(Paginated & Cached)*

All category endpoints support `?page=` and `?limit=` query parameters for full catalog traversal and infinite scroll.  
Responses include `page`, `per_page`, `total`, and `has_more` fields.

| Method | Endpoint | Query Params | Description |
|---|---|---|---|
| `GET` | `/movies` | `?page=1&limit=30` | Movie catalog — scrape or paginate via h5-api |
| `GET` | `/movies/section/{name}` | — | Specific movie section by name |
| `GET` | `/tv-series` | `?page=1&limit=30` | TV series catalog |
| `GET` | `/tv-series/section/{name}` | — | Specific TV series section by name |
| `GET` | `/animation` | `?page=1&limit=30` | Animated series and anime |
| `GET` | `/animation/section/{name}` | — | Specific animation section by name |
| `GET` | `/ranking` | — | Most-watched and top-rated ranking lists *(Cached)* |
| `GET` | `/ranking/section/{name}` | — | Specific ranking section by name |

**Pagination example:**
```
GET /movies?page=2&limit=10
```
```json
{
  "page": 2,
  "per_page": 10,
  "total": 4800,
  "has_more": true,
  "sections": [ ... ]
}
```

---

### 🔍 Search

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/search?q={query}` | Full-text search — returns name, slug, poster, and badge |
| `GET` | `/search/suggest?q={query}` | Autocomplete keyword suggestions |

---

### 📄 Detail & Episodes

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/detail/{slug}` | Full metadata — title, IMDb rating, genres, country, dubs, poster, and basic stream links |
| `GET` | `/detail/{slug}/episodes` | **[NEW]** All seasons and episode lists for TV Series and Anime, with episode titles, thumbnails, and slugs |

**Episode response structure:**
```json
{
  "slug": "my-show-XXXXXXXXXXX",
  "source": "h5-api",
  "total_seasons": 2,
  "seasons": [
    {
      "season": 1,
      "episode_count": 13,
      "episodes": [
        {
          "episode": 1,
          "title": "Pilot",
          "duration": 2520,
          "thumbnail": "https://cdn.example.com/thumb.jpg",
          "slug": "my-show-s1e1-XXXXXXXXXXX",
          "url": "https://moviebox.ph/detail/my-show-s1e1-XXXXXXXXXXX"
        }
      ]
    }
  ]
}
```

> **Note:** Returns `404` with an explanatory message if the slug belongs to a movie rather than a series.

---

### ▶️ Player & Streaming

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/stream/{subject_id}` | Raw stream URL discovery — returns sorted video sources (1080p → 360p) **and** all available subtitle tracks |

**Query parameters:**

| Param | Type | Default | Description |
|---|---|---|---|
| `detail_path` | `string` | required | The slug of the title |
| `se` | `int` | `0` | Season number (0 for movies) |
| `ep` | `int` | `0` | Episode number (0 for movies) |

**Example:**
```
GET /api/stream/123456789?detail_path=my-show-XXXXXXXXXXX&se=1&ep=3
```

**Response structure:**
```json
{
  "subject_id": "123456789",
  "detail_path": "my-show-XXXXXXXXXXX",
  "season": 1,
  "episode": 3,
  "stream_domain": "https://player.example.com",
  "uuid_used": "a1b2c3d4-...",
  "count": 3,
  "sources": [
    { "resolution": "1080p", "format": "mp4", "url": "https://...", "size_bytes": 1073741824 },
    { "resolution": "720p",  "format": "mp4", "url": "https://...", "size_bytes": 536870912 },
    { "resolution": "480p",  "format": "mp4", "url": "https://...", "size_bytes": 268435456 }
  ],
  "subtitles": [
    { "language": "en", "label": "English", "format": "vtt", "url": "https://.../en.vtt" },
    { "language": "es", "label": "Spanish", "format": "srt", "url": "https://.../es.srt" }
  ]
}
```

> **Dynamic UUID:** The `uuid_used` field shows which player session token was active. Tokens are automatically refreshed every 30 minutes — no manual intervention needed.

---

## 🧪 Test Suite — `verify.py`

The included `verify.py` is a full-featured colorized CLI verification tool. Run it any time to confirm the entire API is healthy end-to-end.

```bash
python verify.py
```

**What it covers:**

| Section | Tests |
|---|---|
| **1 · Static Endpoints** | All home, category, and ranking routes — status codes, section counts, poster resolution |
| **2 · Pagination** | `page` / `per_page` echo, `has_more` key, actual item counts across multiple endpoints |
| **3 · Search** | Suggest autocomplete and full-text search result validation |
| **4 · Deep-Link Chain** | Fully dynamic `search → /detail → /episodes → /api/stream` pipeline — zero hardcoded slugs or IDs |
| **5 · Cache Benchmark** | Two rapid-fire requests to `/home` and `/ranking` comparing wall-clock times to verify cache is active |

**Sample output:**
```
────────────────────────────────────────────────────
  4 · DYNAMIC DEEP-LINK CHAIN
────────────────────────────────────────────────────
  ┌─ Step 4.0 · Search for 'batman' to grab a live slug
    ✔ slug extracted: 'batman-the-animated-series-XXXXXXXXXXX'
  ┌─ Step 4.1 · GET /detail/batman-the-animated-series-...
    ✔ subjectId extracted: 987654321
  ┌─ Step 4.2 · GET /detail/.../episodes
    ✔ episodes: 4 season(s) found via 'h5-api'
  ┌─ Step 4.3 · GET /api/stream/987654321?detailPath=...
    ✔ stream: 3 source(s) returned
    ✔ subtitles: 2 track(s) found

════════════════════════════════════════════════════
  RESULTS
════════════════════════════════════════════════════
  ✔ Passed :  38
  ⚠ Warned :   2
  ✘ Failed :   0
════════════════════════════════════════════════════
```

Exit code `0` = all tests passed. Exit code `1` = one or more failures (CI-friendly).

---

## ⚡ Performance Notes

- **Cached endpoints** (`/home`, `/ranking`, `/movies` page 1, etc.) respond in **< 5 ms** on repeat calls within the 15-minute TTL window.
- **Fresh scrape** of a category page takes roughly **2–4 seconds** depending on upstream latency.
- **Stream resolution** (`/api/stream/`) typically completes in **1–2 seconds** including player-domain lookup.
- The cache is **per-process in-memory** — it resets on server restart. For multi-worker deployments, consider replacing the TTL dict with Redis.

---

## 📁 Project Structure

```
MovieBox-API/
├── main.py          # FastAPI application — all endpoints, scrapers, and helpers
├── verify.py        # End-to-end colorized CLI test suite
├── requirements.txt # Python dependencies
└── README.md        # This file
```

---

## 🤝 Contributing

Contributions, issues, and feature requests are welcome!

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/my-new-feature`
3. Commit your changes: `git commit -m 'Add some feature'`
4. Push to the branch: `git push origin feature/my-new-feature`
5. Open a Pull Request

---

## 📜 License

Distributed under the **MIT License**. See [LICENSE](LICENSE) for more information.

---

<p align="center">
  Made with ❤️ for the Streaming Community
</p>
