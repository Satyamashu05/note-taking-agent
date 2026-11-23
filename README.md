# Video Note-Taking Agent

A distributed system that turns a YouTube video into study notes and publishes them to Notion. The notes have a summary, sections with bullet points, and real frames from the video with captions. You click **Take Notes** on a YouTube video, and the job is queued and processed by a pool of workers. The finished page appears in your Notion workspace.

## Features

- **Take Notes** button inside the YouTube player, with optional instructions such as "focus on formulas".
- One-time Notion sign-in (OAuth). The popup shows live progress and a **View in Notion** link.
- Four-stage pipeline: transcript → LLM notes → frame capture → Notion page.
- Captions are used when the video has them; otherwise the audio is transcribed with Whisper.
- Every image is a real frame from the video. Frames that show nothing useful are dropped or searched again.
- Fault tolerance: retries with backoff on HTTP 429, recovery from worker crashes, a dead-letter queue, and text-only pages when frames fail.
- One rate limit for the LLM, shared by all workers, with Groq and OpenRouter as backups.
- Autoscaling worker pool (2–5 workers) and a live ops dashboard.

## Architecture

```
Chrome extension ──HTTP──► API gateway (FastAPI) ──► RabbitMQ ──► worker pool (2–5)
                                  │                                   │
                                  └────────────► Redis ◄──────────────┤ job state, locks, rate limit
                                                                      └──► YouTube · Gemini/Groq · Notion
```

| Component | Role |
|---|---|
| `extension/` | Chrome extension (Manifest V3): button, Notion OAuth, popup |
| `gateway/` | REST API, health check, live dashboard. Does no pipeline work |
| RabbitMQ | Durable job queue, retry queues, dead-letter queue |
| Redis | Job state, worker locks, shared LLM rate limiter |
| `worker/` + `pipeline/` | Run the four pipeline stages |
| `autoscaler/` | Scales the number of workers with the queue length |

## Tech stack

Python 3.12 · FastAPI · RabbitMQ · Redis · yt-dlp · ffmpeg · Gemini (Groq, OpenRouter as backups) · Notion API · Docker Compose · Jenkins

## Prerequisites

- Docker with Docker Compose
- Google Chrome
- A Gemini API key: <https://aistudio.google.com>
- Optional: a Groq API key (backup LLM and speech-to-text): <https://console.groq.com>
- A **public** Notion integration (<https://www.notion.so/profile/integrations>) with this redirect URI:
  `https://ajfdhcigbnpimkjapallphbmbdfmlkpl.chromiumapp.org/`

## Getting started

```bash
git clone https://github.com/Satyamashu05/note-taking-agent.git
cd note-taking-agent
cp .env.example .env          # then fill in the keys (see Configuration)
docker compose up --build -d
curl http://localhost:8000/healthz      # {"status": "ok", "redis": "ok", "rabbitmq": "ok"}
```

Load the extension:

1. Open `chrome://extensions` and turn on **Developer mode**.
2. Click **Load unpacked** and choose the `extension/` folder. No build step is needed.

## Usage

1. Open any YouTube video (up to 20 minutes) and click **Take Notes** in the player.
2. The first time, sign in to Notion and allow access.
3. Open the extension's popup to follow the job: Queued → Transcribing → Summarizing → Extracting frames → Publishing → Done.
4. Click **View in Notion**.

- **Base URL:** `http://localhost:8000`
- **Dashboard:** `http://localhost:8000/dashboard`
- **RabbitMQ UI:** `http://localhost:15672` (user `app`, password `app`)

**Default Notion destination:** if no `notion_parent_id` is sent (the extension never sends one), the page is created as a new **private top-level page** in the workspace you connected. The page title ends with the job's short ID, for example `Raft Consensus [1a2b3c4d]`.

## Configuration

All settings are in `.env`. `.env.example` lists them all with working defaults. The required ones:

| Variable | Default | Meaning |
|---|---|---|
| `GEMINI_API_KEY` | – | Gemini API key (**required**) |
| `LLM_MODEL` | `gemini-3.5-flash` | Model that writes the notes |
| `LLM_RPM` | `5` | Rate limit for `LLM_MODEL`, shared by all workers |
| `LLM_CHECK_MODEL` / `LLM_CHECK_RPM` | `gemini-3.5-flash-lite` / `15` | Model and rate limit for checking frames |
| `NOTION_CLIENT_ID` / `NOTION_CLIENT_SECRET` | – | Notion OAuth credentials (**required**) |
| `RABBITMQ_URL` | `amqp://app:app@rabbitmq:5672/` | Broker connection string |
| `REDIS_URL` | `redis://redis:6379/0` | State store connection string |
| `MAX_ATTEMPTS` | `5` | Attempts per stage before the job goes to the dead-letter queue |
| `BACKOFF_SECONDS` | `10,30,90,90` | Wait before retry 1, 2, 3, … |
| `GROQ_API_KEY` | – | Optional: backup LLM and Whisper for videos without captions |
| `LLM_PROVIDERS` | `gemini,groq,openrouter` | Order in which LLM providers are tried. Providers without a key are skipped |
| `SCALE_MIN` / `SCALE_MAX` | `2` / `5` | Autoscaler limits |

The `FAULT_*` variables are testing switches for the failure demos below. Leave them empty for normal use.

## API

| Method | Path | Description |
|---|---|---|
| `POST` | `/jobs` | Submit `{youtube_url, notion_token, notion_parent_id?, custom_prompt?}` → `202` with `job_id` |
| `GET` | `/jobs/{id}` | Status, progress, title, duration, warnings, error (`404` if unknown) |
| `GET` | `/jobs/{id}/results` | Final result with the Notion page URL (`409` until the job is finished) |
| `GET` | `/healthz` | `200` if Redis and RabbitMQ are reachable, else `503` |
| `GET` | `/dashboard` | Live ops dashboard |

Errors are JSON: `{"error": {"code": "...", "message": "..."}}`.

## Failure demos

Edit `.env`, then run `docker compose up -d` to apply.

| Scenario | How to trigger it |
|---|---|
| Rate limit (429) with backoff | `FAULT_STAGE=SUMMARIZING`, `FAULT_KIND=429`, `FAULT_TIMES=2` |
| Worker crash mid-job | `docker kill $(docker compose ps -q worker \| head -1)` while a job is running |
| Graceful degradation | `FAULT_FRAME_FAILURES=2` (first 2 frames fail) or `all` (text-only page) |
| Dead-letter queue | Submit a private or deleted video, or set `FAULT_TIMES=99` |
| Scaling | `docker compose up -d --scale worker=4` |
| Duplicate-page check | `FAULT_CRASH_AFTER_PAGE=1` (crashes right after the page is created) |

## Project structure

```
extension/    Chrome extension: content.js, background.js, popup.*
gateway/      app.py (REST API), dashboard.py + dashboard.html
worker/       main.py (consumer), handler.py (locks, retries, DLQ), stages.py
pipeline/     transcript, audio_transcript, summarize, frames, verify, frame_agent, notion, llm
common/       config, broker, jobs (Redis state), limiter, errors
autoscaler/   scales the worker containers
ci/           Jenkins smoke-test files
Jenkinsfile   CI/CD pipeline
```

## CI/CD

Jenkins builds the image, lints it with ruff, and starts the whole stack as a throwaway smoke test. Pushes to `main` are then deployed with `docker compose up -d --build`. Secrets are stored as Jenkins credentials, never in the repository.

## Team

- Satyam Dewangan (IMT2023545)
- Yashub Goel (IMT2023117)
