# Agent Relay

Agent Relay is a small FastAPI service for registering agents, delivering one
task at a time, and recording results. PostgreSQL persists the queue and
attempts, while workers execute tasks on their own
machines. The included worker deterministically returns `input.upper()`.

## Run it

```bash
docker compose up --build
```

This starts two services: `postgres` (PostgreSQL 17, data in the
`postgres-data` volume) and `api` (this app, built from the `Dockerfile`). The
API reaches the database at the Compose service hostname `postgres`
(`RELAY_DATABASE_URL=postgresql+psycopg://relay:relay@postgres:5432/agent_relay`).

Open <http://127.0.0.1:8000/> for the token-based local dashboard. To run the
API outside Docker, start only the database (`docker compose up -d postgres`)
and run `uv run uvicorn main:app --reload`; it defaults to
`postgresql+psycopg://relay:relay@localhost:5432/agent_relay`. `GET /health` is
a liveness check and `GET /ready` verifies database connectivity and schema (it
queries the real tables, so a wiped volume reports not-ready instead of passing
with zero tables).

Register two identities and send a task:

```bash
alice=$(curl -sS -X POST http://127.0.0.1:8000/api/v1/agents \
  -H 'content-type: application/json' -d '{"name":"alice"}')
bob=$(curl -sS -X POST http://127.0.0.1:8000/api/v1/agents \
  -H 'content-type: application/json' -d '{"name":"uppercase"}')
```

The response contains each agent's secret `token` once. Keep it outside source
control. Use `Authorization: Bearer <token>` for all subsequent API calls;
registration is the only unauthenticated endpoint. For a shared installation,
set `RELAY_ENROLLMENT_SECRET` and send it as `X-Enrollment-Secret` when
registering.

## Run the deterministic worker

The worker can register itself and save credentials in a mode-0600 JSON file:

```bash
uv run python main.py worker \
  --base-url http://127.0.0.1:8000 \
  --name uppercase \
  --credentials ./uppercase-credentials.json \
  --worker-id laptop-1
```

For failure/redelivery demonstrations, make local execution intentionally slow
and stop the process after one completion:

```bash
uv run python main.py worker --credentials ./uppercase-credentials.json \
  --slow-seconds 75 --worker-id slow-laptop
```

The worker heartbeats during long work. Killing it leaves the claim leased;
after the 60-second lease expires, another worker can claim the task with a new
token and incremented attempt number. `RELAY_LEASE_SECONDS` and
`RELAY_MAX_ATTEMPTS` are configurable server settings.

An existing credential can also be supplied explicitly (the token is not
written to disk):

```bash
uv run python main.py worker --agent-id agent_123 --token agt_… --worker-id laptop-2
```

## Storage and delivery behavior

`database.py` contains SQLAlchemy models, the PostgreSQL engine, and the
`write_transaction` helper. `storage.py` contains task/claim/recovery
operations; routes and request models are kept in `main.py` and `schemas.py`.
Every state change locks the task row before its attempts. Claims and recovery
use `FOR UPDATE SKIP LOCKED`, so concurrent workers across processes never wait
on or share a task; idempotent task creation takes a transaction-scoped
advisory lock on the sender and key.

Claims are at-least-once and leased for 60 seconds by default. Heartbeats extend
an active lease. A completion or failure must include the recipient's bearer
token and claim token. Repeating the exact terminal request with that claim
token is idempotent; a stale token or different result receives `409`.

## Verify

The test suite covers the main protocol, sender/recipient access boundaries,
hashed claim-token behavior, idempotent terminal retries, concurrent claims,
lease expiry before and after recovery, pagination/error shape, and dashboard
asset serving:

```bash
docker compose up -d postgres
uv run pytest -q
```

Tests default to the scratch `agent_relay_test` database, which
`docker/postgres-init.sql` creates on the volume's first start, so they never
reset the API's `agent_relay` database. The fixture drops and recreates all
tables on whatever `RELAY_DATABASE_URL` points at, so never point it at a
database with data you need.

`test_compose_integration.py` runs acceptance scenario 1 against the running
stack over HTTP and then reads the resulting rows from its PostgreSQL database
(read-only). It is skipped unless `RELAY_API_URL` is set:

```bash
docker compose up --build -d
RELAY_API_URL=http://127.0.0.1:8000 uv run pytest -q test_compose_integration.py
```

This project intentionally does not include Kubernetes, CI, external brokers,
or an LLM. Those are deployment concerns rather than part of the relay protocol.

## CI/CD

`.github/workflows/ci.yml` has two jobs:

1. **test** starts a PostgreSQL service and runs `test_agent_relay.py` against
   a scratch `agent_relay_test` database. It then starts the API against
   `agent_relay` and runs `test_compose_integration.py` against it over HTTP.
2. **deploy** runs only if `test` passed. It builds `agent-relay:<UTC
   timestamp>-<short sha>` (a new tag per run), loads it into the kind cluster,
   applies `k8s/` with that image, and waits for the rollout. If the rollout
   or the dashboard smoke test fails, it runs `kubectl rollout undo`. A failing
   test skips the whole job, so the version already in the cluster keeps
   running.

The deploy job targets a kind cluster on your machine, so it runs only under
act (actor `nektos/act`); on GitHub the tests run and `deploy` is skipped.

Run it locally with [act](https://nektosact.com) against a kind cluster:

```bash
kind create cluster --name agent-relay   # once
act push -P ubuntu-latest=catthehacker/ubuntu:act-latest \
  --container-architecture linux/arm64 \
  -s KUBECONFIG_B64="$(kind get kubeconfig --name agent-relay | base64)"
```

act mounts the Docker socket into the job, so `docker build` and
`kind load docker-image` use your local Docker. The job container shares the
Docker host network, so the kubeconfig's `127.0.0.1:<port>` API server address
is reachable. On an Intel machine, use `linux/amd64`.
