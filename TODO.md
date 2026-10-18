# Garblo Backend MVP — Implementation Checklist

Last updated: 2026-10-18

## Implemented — runtime verification pending

- [x] Initialize the Python backend structure, development tooling and environment template.
- [x] Add FastAPI routes, JWT authentication, consent and ownership enforcement.
- [x] Implement PostgreSQL models and initial Alembic migration.
- [x] Implement RustFS-compatible object storage and signed-download abstraction.
- [x] Implement PostgreSQL-backed AI job worker, Azure provider adapter and approval flow.
- [x] Add the same-origin HTML API mock at `/mock`.
- [x] Add API documentation and initial auth/Azure-unavailable tests.

## Verification blocker

- [ ] Install `python3.12-venv` (and `python3-pip` if needed), then create `.venv`, install `.[dev]`, and run `pytest -q`.

## Deferred until Docker is available

- [ ] Add and verify Dockerfile, Compose stack, health checks and persistent volumes.
- [ ] Run the full stack with PostgreSQL and RustFS containers.

## Acceptance checklist

- [ ] A user can register, log in, record AI consent and access only their own resources.
- [ ] An image upload produces an asset record and uses object storage rather than PostgreSQL blobs.
- [ ] AI routes return `503` before valid Azure configuration is supplied.
- [ ] With Azure configured, a worker can process catalogue/lookbook jobs and create a pending approval.
- [ ] `/mock` supports the full manual API-test journey and is disabled outside development by default.
