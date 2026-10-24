# Multilingual News Platform API

A self-hostable backend for a multilingual news platform. It ingests RSS feeds, extracts
article text, organizes content with a topic tree, and serves language-aware feeds and
search. Authentication is handled by Clerk-issued JWTs.

Built with **FastAPI**, **MongoDB (Motor)**, and **Clerk**. No Docker or external vector
database is required.

## Contents

- [Features](#features)
- [Architecture](#architecture)
- [Requirements](#requirements)
- [Install on another computer](#install-on-another-computer)
- [Configure Clerk](#configure-clerk)
- [Configuration reference](#configuration-reference)
- [Run the API](#run-the-api)
- [Authentication and roles](#authentication-and-roles)
- [API overview](#api-overview)
- [Example requests](#example-requests)
- [Ingestion pipeline](#ingestion-pipeline)
- [Search and embeddings](#search-and-embeddings)
- [Data model and indexes](#data-model-and-indexes)
- [Testing and quality](#testing-and-quality)
- [Troubleshooting](#troubleshooting)
- [Security notes](#security-notes)

## Features

- **Clerk resource-server authentication** — RS256 session tokens are verified against the
  Clerk JWKS endpoint. The frontend owns signup, login, and logout.
- **Lazy profile synchronization** — the first authenticated request provisions a MongoDB
  user document with a `reader` role, preferred language, and empty interests.
- **Role-based access control** — `reader` (default), `editor`, and `admin` roles enforce
  who can manage sources, topics, communities, ingestion, and role assignments.
- **News sources and RSS ingestion** — create sources, run ingestion jobs, deduplicate by
  URL, and track per-job statistics.
- **Article extraction** — `trafilatura` with a BeautifulSoup fallback, language detection,
  content truncation, and deterministic topic matching through topic keywords.
- **Multilingual search** — tokenized, language-scoped keyword search plus semantic search
  over stored embeddings. A deterministic hashed embedding works out of the box; an optional
  multilingual sentence-transformers model can be enabled.
- **Topic tree, communities, and feeds** — nested topics, community topic curation, community
  editor assignment, and a personalized feed combining language, interests, and community.
- **Article translations** — editors can store per-language translations and clients can
  fetch them.
- **Quality gates** — pytest with an in-memory MongoDB double and no live services, plus
  Ruff lint and format checks in CI.

## Architecture

```
Client (Clerk-authenticated frontend)
        │  Authorization: Bearer <clerk_jwt>
        ▼
FastAPI /api/v1  ──►  core/security.py (PyJWKClient + PyJWT RS256 verification)
        │
        ├── api/v1/*            HTTP validation, status codes, RBAC dependencies
        ├── services/*          RSS, extraction, embeddings, search, ingestion
        └── repositories/*      all MongoDB queries
                │
                ▼
        MongoDB (Motor, async)
```

Project layout:

```
multilingual-news/
├── app/
│   ├── main.py                  # App factory, lifespan, CORS, error handlers
│   ├── config.py                # pydantic-settings configuration
│   ├── database.py              # Motor client, dependencies, index definitions
│   ├── api/
│   │   ├── deps.py              # Repository/service providers, profile + RBAC deps
│   │   └── v1/
│   │       ├── users.py
│   │       ├── health.py
│   │       ├── sources.py
│   │       ├── ingestion.py
│   │       ├── articles.py
│   │       ├── topics.py        # Topic tree + /search routes
│   │       ├── communities.py
│   │       └── feeds.py
│   ├── core/
│   │   ├── security.py          # Clerk JWT verification
│   │   ├── exceptions.py        # AppError hierarchy mapped to JSON responses
│   │   └── utils.py             # IDs, slugs, URL safety, cosine similarity
│   ├── schemas/                 # Pydantic request/response models
│   ├── services/
│   │   ├── rss_service.py
│   │   ├── extraction_service.py
│   │   ├── embedding_service.py
│   │   ├── search_service.py
│   │   └── ingestion_service.py
│   └── repositories/
│       ├── users.py
│       ├── sources.py
│       ├── articles.py
│       ├── topics.py
│       ├── communities.py
│       └── ingestion_jobs.py
├── tests/                       # pytest suite (76 tests, no external services)
├── .github/workflows/ci.yml
├── .env.example
├── pyproject.toml
├── requirements.txt
└── README.md
```

## Requirements

- Python **3.11 or newer** (the project is tested on 3.11, 3.12, and 3.13)
- MongoDB **6.0 or newer** running locally, or a MongoDB Atlas connection string
- A Clerk application (publishable key, secret key, and issuer URL)
- Git

No Docker is required. All dependencies are pinned in `requirements.txt`.

## Install on another computer

These steps assume a clean machine with Python and Git installed. Commands are shown for
Windows PowerShell and macOS/Linux.

### 1. Install Python and Git

**Windows**

1. Install Python 3.11+ from https://www.python.org/downloads/ and enable
   **Add python.exe to PATH** during setup.
2. Install Git from https://git-scm.com/download/win.
3. Open a new PowerShell window and verify:

```powershell
python --version
git --version
```

**macOS**

```bash
brew install python@3.12 git
python3 --version
```

**Ubuntu / Debian**

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git
python3 --version
```

### 2. Get the code

```bash
git clone https://github.com/source-trust-me-bro/Backend.git
cd Backend
```

If the repository is private, authenticate first with GitHub CLI (`gh auth login`) or use
an SSH remote.

### 3. Create a virtual environment and install dependencies

**Windows PowerShell**

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

If PowerShell blocks activation, allow scripts for the current session:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
.\.venv\Scripts\Activate.ps1
```

**Windows Command Prompt**

```bat
python -m venv .venv
.venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt
```

**macOS / Linux**

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### 4. Start MongoDB

Use one of the following options.

**Option A: local MongoDB (recommended for development)**

- **Windows:** download the MongoDB Community Server installer from
  https://www.mongodb.com/try/download/community, install it, and start the MongoDB service
  (`net start MongoDB` in an elevated terminal). The default URI is
  `mongodb://localhost:27017`.
- **macOS:**

```bash
brew tap mongodb/brew
brew install mongodb-community@7.0
brew services start mongodb-community@7.0
```

- **Ubuntu/Debian:** follow the official package instructions at
  https://www.mongodb.com/docs/manual/administration/install-on-linux/, then:

```bash
sudo systemctl enable --now mongod
```

- **Without a service manager:** run `mongod --dbpath /path/to/data` and keep it running.

**Option B: MongoDB Atlas**

Create a free cluster at https://www.mongodb.com/atlas, create a database user, allow your
IP address, and copy the connection string. It looks like:

```
mongodb+srv://<user>:<password>@<cluster>.mongodb.net/?retryWrites=true&w=majority
```

Use that value for `MONGODB_URI`. Do not commit it.

### 5. Create the environment file

**Windows PowerShell**

```powershell
Copy-Item .env.example .env
```

**macOS / Linux**

```bash
cp .env.example .env
```

Open `.env` and set at minimum:

```dotenv
MONGODB_URI=mongodb://localhost:27017
MONGODB_DATABASE=multilingual_news
CLERK_ISSUER=https://your-instance.clerk.accounts.dev
CLERK_PUBLISHABLE_KEY=pk_test_your_publishable_key
CLERK_SECRET_KEY=sk_test_your_secret_key
NEWS_LANGUAGES=en,fi,sv
```

All supported settings are documented in [Configuration reference](#configuration-reference).
The `.env` file is git-ignored and must never be committed.

### 6. Verify the installation

```bash
# Import check
python -c "from app.main import app; print('app loaded')"

# Test suite (no MongoDB or Clerk account is needed)
pytest -q

# Lint and format checks used by CI
ruff check .
ruff format --check .
```

### 7. Start the API

```bash
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Then open:

- API documentation: http://127.0.0.1:8000/docs
- Liveness: http://127.0.0.1:8000/api/v1/health
- Readiness: http://127.0.0.1:8000/api/v1/health/ready

The readiness endpoint returns `503` until MongoDB is reachable. Indexes are created
automatically when the application starts.

### 8. Confirm an authenticated request

Obtain a Clerk session token from your frontend (call `getToken()` in the Clerk SDK) or from
the Clerk dashboard, then:

```bash
curl -H "Authorization: Bearer <clerk-session-token>" \
  http://127.0.0.1:8000/api/v1/users/me
```

The first successful call creates the user's MongoDB profile with the `reader` role.

### Updating an existing installation

```bash
git pull
source .venv/bin/activate        # Windows: .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Configure Clerk

1. Create an application at https://dashboard.clerk.com.
2. Copy the **publishable key**, **secret key**, and **issuer URL** from the API keys page.
   The issuer looks like `https://your-instance.clerk.accounts.dev`.
3. Put the values in `.env`. The API derives the JWKS URL as
   `<issuer>/.well-known/jwks.json`; set `CLERK_JWKS_URL` to override it.
4. Add your frontend origin to `CORS_ORIGINS`.
5. Optional: in the Clerk dashboard, add `email`, `name` (or `first_name` and `last_name`)
   to the session token template so the API can populate profile fields from the token.

The backend never handles passwords or sessions. It only validates the bearer token sent by
the frontend.

## Configuration reference

Settings are loaded from environment variables and `.env`. See `.env.example`.

| Variable | Default | Purpose |
| --- | --- | --- |
| `APP_NAME` | `multilingual-news` | Service name |
| `APP_ENV` | `development` | Environment label |
| `DEBUG` | `false` | Debug flag |
| `API_HOST` | `127.0.0.1` | Host used by helper tooling |
| `API_PORT` | `8000` | Port used by helper tooling |
| `LOG_LEVEL` | `INFO` | Logging level |
| `MONGODB_URI` | `mongodb://localhost:27017` | Mongo connection string |
| `MONGODB_DATABASE` | `multilingual_news` | Database name |
| `MONGODB_TIMEOUT_MS` | `5000` | Driver selection/connect timeout |
| `CLERK_PUBLISHABLE_KEY` | — | Frontend key (informational for the API) |
| `CLERK_SECRET_KEY` | — | Reserved for future Clerk Backend API calls |
| `CLERK_ISSUER` | — | Clerk instance issuer |
| `CLERK_JWKS_URL` | derived | Optional explicit JWKS URL |
| `CLERK_JWKS_CACHE_SECONDS` | `300` | JWKS cache lifetime |
| `NEWS_LANGUAGES` | `en,fi,sv` | Comma-separated supported language codes |
| `DEFAULT_LANGUAGE` | `en` | Preferred language for new profiles |
| `CORS_ORIGINS` | `http://localhost:3000,http://localhost:5173` | Allowed browser origins |
| `EMBEDDING_MODEL` | multilingual MiniLM | Sentence-transformers model name |
| `EMBEDDING_DIM` | `384` | Hashed embedding dimension |
| `ENABLE_SENTENCE_TRANSFORMERS` | `false` | Use a real transformer model |
| `INGESTION_MAX_ITEMS` | `20` | Default entries per ingestion run |
| `FETCH_ARTICLE_CONTENT` | `true` | Extract full text from article URLs |
| `MAX_CONTENT_CHARS` | `20000` | Stored article content limit |
| `HTTP_TIMEOUT_SECONDS` | `15` | Outbound HTTP timeout |
| `SEARCH_MAX_TOKENS` | `8` | Maximum tokens in keyword search |
| `SEMANTIC_CANDIDATE_LIMIT` | `500` | Candidates scored per semantic query |
| `STARTUP_DB_CHECK` | `true` | Connect and create indexes on startup |

Supported languages are enforced for sources, topics, communities, user preferences, and
language query parameters. Unsupported values return `422` in request bodies or `400` in
query parameters.

## Run the API

Development:

```bash
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Production-style (multiple workers):

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4
```

Keep `/api/v1/health` behind your load balancer liveness check and `/api/v1/health/ready`
behind readiness checks.

## Authentication and roles

The API is an OAuth 2.0 **resource server**. Every request except the health endpoints must
include a Clerk session token:

```http
Authorization: Bearer <clerk_session_token>
```

Verification steps in `app/core/security.py`:

1. Reject anything that is not an RS256 JWT with `typ: JWT`.
2. Resolve the signing key from the cached Clerk JWKS client.
3. Validate the signature and the `exp`, `iat`, `sub`, and `iss` claims (30 second leeway).
4. Extract identity claims into a `ClerkPrincipal`.

The first authenticated request upserts a local profile keyed by `clerk_user_id`.

| Role | Capabilities |
| --- | --- |
| `reader` | Browse sources, articles, topics, communities, feeds, and search |
| `editor` | Reader capabilities plus source CRUD, ingestion, topic management, community curation, and translations |
| `admin` | Editor capabilities plus assigning roles through `PATCH /users/{clerk_user_id}/roles` |

New profiles start as `reader`. A user cannot grant themselves a role: `roles` in
`PATCH /users/me` is honored only when the caller is already an admin, and role changes for
other users require the dedicated admin endpoint.

## API overview

All routes are under `/api/v1`. Everything except the health routes requires a valid bearer
token.

| Method | Path | Access | Description |
| --- | --- | --- | --- |
| GET | `/health` | Public | Liveness |
| GET | `/health/ready` | Public | MongoDB readiness |
| GET | `/users/me` | Reader | Current profile (lazily provisioned) |
| PATCH | `/users/me` | Reader | Update language, community, and topics; roles only for admins |
| PATCH | `/users/{clerk_user_id}/roles` | Admin | Assign roles |
| POST | `/sources` | Editor | Register a source |
| GET | `/sources` | Reader | List and filter sources |
| GET | `/sources/{id}` | Reader | Fetch a source |
| PATCH | `/sources/{id}` | Editor | Update a source |
| DELETE | `/sources/{id}` | Editor | Delete a source |
| POST | `/ingestion/jobs` | Editor | Run an RSS ingestion job |
| GET | `/ingestion/jobs` | Editor | List jobs |
| POST | `/ingestion/articles` | Editor | Ingest one article by URL or inline content |
| GET | `/articles` | Reader | Filter, paginate, and keyword-search articles |
| GET | `/articles/trending` | Reader | Most-read articles this week, padded with the newest (`limit`, `language`) |
| GET | `/articles/{id}` | Reader | Article detail |
| GET | `/articles/{id}/related` | Reader | Related articles by shared topics |
| GET | `/articles/{id}/translations` | Reader | Stored translations |
| POST | `/articles/{id}/translations` | Editor | Create or update a translation |
| GET | `/topics/tree` | Reader | Nested topic tree |
| POST | `/topics` | Editor | Create a topic |
| PATCH | `/topics/{id}` | Editor | Update a topic |
| GET | `/search` | Reader | Keyword search (`q` required) |
| POST | `/search/semantic` | Reader | Semantic search |
| POST | `/communities` | Editor | Create a community |
| GET | `/communities` | Reader | List communities |
| GET | `/communities/{id}` | Reader | Community detail |
| PATCH | `/communities/{id}` | Editor | Update topics and editors |
| GET | `/communities/{id}/feed` | Reader | Community-curated feed |
| GET | `/feeds/personalized` | Reader | Personalized feed |
| GET | `/feeds/following` | Reader | Newest articles from followed sources |
| GET | `/library/state` | Reader | Ids of saved and downloaded articles |
| GET | `/library/saved`, `/library/downloaded`, `/library/history` | Reader | Paginated article lists |
| PUT | `/library/{article_id}/saved` | Reader | Save or unsave (`{"value": bool}`) |
| PUT | `/library/{article_id}/downloaded` | Reader | Mark or unmark as downloaded |
| POST | `/library/{article_id}/read` | Reader | Record a read (history and trending) |
| DELETE | `/library/history`, `/library/downloaded` | Reader | Clear history or downloads |
| GET | `/follows/sources` | Reader | Sources the caller follows |
| PUT | `/follows/sources/{source_id}` | Reader | Follow or unfollow a source |
| POST | `/feedback` | Reader | Send feedback to the team |
| GET | `/feedback` | Editor | List feedback, newest first |

## Example requests

```bash
export TOKEN="<clerk-session-token>"
export API="http://127.0.0.1:8000/api/v1"

# Profile (creates the MongoDB record on first call)
curl -H "Authorization: Bearer $TOKEN" "$API/users/me"

# Update preferences
curl -X PATCH "$API/users/me" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"preferred_language":"fi","topic_ids":[]}'

# Browse Finnish articles, newest first
curl -H "Authorization: Bearer $TOKEN" "$API/articles?language=fi&page=1&page_size=20"

# Keyword and semantic search
curl -H "Authorization: Bearer $TOKEN" "$API/search?q=climate&language=en"
curl -X POST "$API/search/semantic" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"query":"climate policy","language":"en","limit":10}'
```

Errors use a consistent shape:

```json
{"detail": "Article '...' was not found.", "code": "not_found"}
```

## Ingestion pipeline

1. `POST /ingestion/jobs` loads the source and creates a job record.
2. The RSS service fetches the feed with a shared user agent and timeout, then parses entries.
3. The extraction service fetches each article page, extracts text and metadata, and detects
   the language.
4. Topics are matched deterministically: a topic is assigned when one of its keywords appears
   in the title or summary.
5. An embedding is computed from the title and summary.
6. The article is upserted by URL, so re-running a job updates existing articles instead of
   duplicating them.
7. The job finishes with `{fetched, created, updated, skipped, failed}` statistics. A feed-level
   failure marks the job `failed` and returns `502`.

Jobs execute synchronously within the request so results are deterministic and testable. For
large feeds or many sources, trigger the endpoint from an external scheduler such as cron, a
platform scheduler, or a queue worker.

`POST /ingestion/articles` supports two modes:

- `{"url": "https://..."}` — fetch and extract the page.
- `{"title": "...", "content": "..."}` — store inline content directly.

Both modes optionally accept `source_id`, `language`, `author`, `published_at`, and
`topic_ids`. Inline articles without a URL receive a unique `manual://article/<uuid>` URL.

## Search and embeddings

- **Keyword search** tokenizes the query (up to `SEARCH_MAX_TOKENS`) and requires every token
  to appear in the title, summary, or content, case-insensitively. It works with Finnish,
  Swedish, and English text.
- **Semantic search** embeds the query and scores stored article embeddings with cosine
  similarity over recent candidates, optionally restricted by language and community topics.

By default a deterministic feature-hashed embedding is used: word unigrams plus character
trigrams, `EMBEDDING_DIM` dimensions, L2-normalized. It requires no model download and is
fully reproducible in tests.

For cross-lingual semantic similarity, install the optional model and enable it:

```bash
pip install sentence-transformers
```

```dotenv
ENABLE_SENTENCE_TRANSFORMERS=true
EMBEDDING_MODEL=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
```

Embeddings are written during ingestion, so re-run ingestion after switching embedding
backends.

## Data model and indexes

Collections: `users`, `sources`, `articles`, `article_translations`, `topics`, `communities`,
and `ingestion_jobs`.

Indexes created at startup:

- `users.clerk_user_id` (unique), `users.email`
- `sources.feed_url` (unique), `sources` by language and active
- `articles.url` (unique), plus `(source_id, published_at)`, `(language, published_at)`,
  `(topic_ids, published_at)`, and `published_at`
- `article_translations (article_id, language)` (unique)
- `topics.slug` (unique), `topics.parent_id`, `topics.language`
- `communities.slug` (unique), `communities.language`, `editor_user_ids`, `topic_ids`
- `ingestion_jobs (source_id, created_at)`, `status`, `created_at`

## Testing and quality

```bash
# Full suite: uses mongomock-motor and dependency overrides; no Mongo or Clerk needed
pytest -q

# CI gates
ruff check .
ruff format --check .

# Apply formatting
ruff format .
```

The suite covers Clerk RS256 verification (valid, expired, wrong issuer, wrong algorithm,
missing claims), lazy profile provisioning, role escalation attempts, CRUD for every
resource, feed filtering, ingestion success/update/failure paths, RSS parsing, article
extraction, embeddings, URL safety, and the required API surface.

CI (`.github/workflows/ci.yml`) runs Ruff lint, Ruff format check, and pytest on Python
3.11, 3.12, and 3.13.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| `python` is not recognized | Install Python 3.11+ and enable "Add to PATH", or use `py -3.12` on Windows |
| Activation is blocked in PowerShell | Run `Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned` |
| `ModuleNotFoundError: app` | Run commands from the repository root with the virtual environment active |
| `/health/ready` returns 503 | MongoDB is down or `MONGODB_URI` is incorrect |
| `401 unauthorized` with a token | Check `CLERK_ISSUER`, token expiry, and the `Authorization` header |
| `503 auth_not_configured` | Set `CLERK_ISSUER` or `CLERK_JWKS_URL` |
| `403 forbidden` | The endpoint requires the `editor` or `admin` role |
| Ingestion returns 502 | The feed was unreachable or invalid; inspect `GET /ingestion/jobs` |
| Ingestion creates no articles | Entries need a title or extractable page metadata; check logs |
| Semantic results look lexical | Enable sentence-transformers for cross-lingual vectors |
| Port 8000 is in use | Run `uvicorn ... --port 8001` |

## Security notes

- Only RS256 tokens are accepted; `alg: none` and HMAC tokens are rejected before key lookup.
- Issuer, expiry, and signature are validated with a small clock-skew allowance.
- Feed and article fetches reject non-HTTP(S) URLs, loopback addresses, private ranges,
  link-local addresses, and `.local` hosts to reduce SSRF risk. Redirect targets are
  re-validated.
- Roles live in MongoDB and are checked per request; users cannot self-assign roles.
- Secrets belong in `.env`, which is git-ignored. Rotate the Clerk keys if they are ever
  committed or shared.
