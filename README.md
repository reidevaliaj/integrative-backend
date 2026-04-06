# Integrative Backend

FastAPI backend for the Integrative Medicine Journal platform.

## Features

- JWT-based authentication
- PostgreSQL-backed users, magazines, and fake subscriptions
- Seeded magazine data and sample PDF files
- Optional welcome emails through Resend
- Ready for deployment behind Nginx and systemd

## Local setup

```bash
python -m venv .venv
. .venv/Scripts/activate
pip install -r requirements.txt
copy .env.example .env
python scripts/generate_sample_pdfs.py
uvicorn app.main:app --reload
```

The API will be available at `http://localhost:8000`.

## Main routes

- `POST /api/v1/auth/register`
- `POST /api/v1/auth/login`
- `GET /api/v1/auth/me`
- `POST /api/v1/auth/logout`
- `GET /api/v1/magazines/`
- `GET /api/v1/magazines/{slug}`
- `GET /api/v1/subscriptions/plans`
- `GET /api/v1/subscriptions/me`
- `POST /api/v1/subscriptions/subscribe`
- `GET /api/v1/health`

## Deployment notes

Deployment examples are included in:

- `deploy/integrative-backend.service`
- `deploy/ohm.cod-st.com.nginx.conf`
