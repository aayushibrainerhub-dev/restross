## Restaurant API

A FastAPI backend for restaurant operations: staff users, restaurants and tables, menu categories/items, customers, orders/order lines, reservations, and payments.

Run locally:

```bash
uv sync
uv run uvicorn app.main:app --reload
```

The interactive API documentation is at `http://127.0.0.1:8000/docs`; the health endpoint is `GET /health`.

The application uses PostgreSQL. Copy `.env.example` to `.env`, then replace `DATABASE_URL` with the connection URL you provide. For local containers, `docker compose up --build` starts PostgreSQL and the API together.

Dependencies are managed with `uv`; use `uv sync` rather than `pip install`.

Key endpoints are namespaced below `/api/v1`: `/restaurants`, `/menu`, `/customers`, `/orders`, `/reservations`, `/payments`, and `/auth`.
