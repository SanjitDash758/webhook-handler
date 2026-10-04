# Manual Test Checklist

Human-runnable verification of the webhook-handler system.

## Prerequisites

Before running any test:

- [ ] PostgreSQL running on `localhost:5432`
- [ ] Redis (Memurai) running on `localhost:6379`
- [ ] `.env` populated with real credentials
- [ ] FastAPI running: `uvicorn app.main:app --reload`
- [ ] Celery worker running: `celery -A app.workers.celery_app worker --pool=solo --loglevel=info`

