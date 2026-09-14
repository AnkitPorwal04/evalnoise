# M6 local coordinator: runbook and contract

## Deployment boundary

This is a **single-coordinator, loopback-only pilot**, separate from M5. It uses
Python's standard library and SQLite WAL, with no runtime package dependencies.
It authenticates principals but does not isolate mutually hostile local OS users.
Use reviewed workloads only. Docker access remains privileged. Wider network
deployment needs TLS, separate engines/VMs per trust domain, managed identities,
backup/key rotation, monitoring and a separate threat review.

The administrator supplies a fixed catalog, not a public execution API. Model
providers and agent tasks are refused. Catalog images must be immutable local
`sha256:<64 hex>` IDs. Owners submit catalog IDs and cannot supply a command,
image, configuration, pool or filesystem path. The worker independently checks
its own image allowlist and the catalog digest before calling the existing runner.
Pool/image matching is the implemented capability vocabulary, not dynamic CPU
availability or scheduler reservations. Existing runner preflight/audit still apply.

## Provision and operate

Create a JSON catalog with keys such as `calibration`. Each value is exactly
`{"pool":"trusted-local","config":<a reviewed EvalNoise config>}`. Build the
reviewed workload/verifier images first and replace their tags with image IDs.
The catalog can be held outside Git. Never include credentials in it.

```sh
python3 -m evalnoise cluster-init runs/my-cluster --catalog reviewed-catalog.json --trust-config
python3 -m evalnoise cluster-serve runs/my-cluster --port 4179
```

Provisioning requires a new directory. It creates a private (0700) directory,
0600 credential files for `admin`, `alice`, `bob`, `worker`, `worker-b`, an HMAC key,
and a SQLite database containing only credential hashes. These names are local
pilot identities, not an account registration service. Keep the directory ignored
and never publish it. Do not reuse these credentials for anything else.

In another terminal:

```sh
python3 -m evalnoise cluster-submit --credential runs/my-cluster/alice.json --catalog calibration --request-key study-001
python3 -m evalnoise cluster-worker --credential runs/my-cluster/worker.json --output runs --trust-config
python3 -m evalnoise cluster-status --credential runs/my-cluster/alice.json
python3 -m evalnoise cluster-audit --credential runs/my-cluster/admin.json
```

Workers process **one job per invocation**, or return idle. There is no hidden
polling daemon or retry loop. Starting the command again is an explicit action.
Use a different worker credential for another worker process. On the same Docker
engine, M2's advisory lock still rejects overlapping EvalNoise runs. The retained
worker run directories are viewable with M5; the coordinator database is not.

`cluster-cancel --credential ... --job <ID>` cancels an active owned job.
`cluster-purge --credential ... --job <ID>` removes a completed owned bundle,
retaining job metadata, receipt and audit history. Ctrl+C stops the service.

## Lease and quota semantics

- Owner-scoped idempotency keys return the existing job; changing the catalog for
  a used key is refused. Different owners can reuse the same key.
- `BEGIN IMMEDIATE` transactions serialize claims, quota reservations, acceptance
  and administrative changes. Each worker holds at most one active lease.
- A lease lasts 30 seconds and is renewed approximately every 10 seconds by the
  worker. An expired lease is requeued at the next claim, up to three attempts.
  It is not a background recovery timer. Attempts have distinct capabilities.
- A stale, cancelled or revoked worker cannot complete. A repeated identical
  completion returns its original receipt; a changed completion is refused.
- Execution is **not exactly once**. A disconnected or killed worker can leave
  work running. Heartbeat failure requests cooperative cancellation; operators
  use M2's manifest-scoped diagnosis/cleanup, never global Docker pruning.
- Explicit worker execution failures mark the job failed; only lease loss permits
  automatic requeue on a later claim. Earlier local artifacts are not overwritten.
- Per owner: at most 2 active jobs, 100 retained job records, 1000 cumulatively
  reserved trials across attempts, and 64 MiB accepted bundle data. Per bundle:
  16 MiB. Trial reservations are not refunded after a lost attempt. These are
  execution/storage quotas, **not monetary or subscription budgets**.
- Purging releases bundle storage, not historical job/trial quotas. This bounded
  pilot deliberately has no quota-reset or account-management endpoint.

## HTTP contract

All requests need `Authorization: Bearer <token>`. JSON POST bodies have exact
field sets. Unknown routes/fields are refused. Requests are bounded and timed out;
chunked or ambiguous lengths, non-finite/duplicate JSON keys, bad Host headers,
and browser origins are refused. No CORS or cookie authentication is offered.
Only `http://127.0.0.1:PORT` is accepted by the client.

Owner routes: `POST /submit` (`catalog`, `request_key`), `GET /jobs`,
`GET /artifact/<job>`, `POST /cancel` or `/purge` (`job`).
Worker routes: `POST /claim` (`{}`), `/heartbeat` and `/fail` (`job`, `lease`),
`/complete` (`job`, `lease`, `bundle`).
Admin routes: `GET /audit`, `POST /revoke` (`principal`).
The `Client` helper can call the admin revocation route; credentials must not be
put in shell history. Revocation cancels active owned/leased jobs immediately.

Responses use 200 for successful operations, 400 for malformed evidence, 401/403
for credential/role or transport refusal, 404 for inaccessible objects, 409 for
fencing/state conflicts, 413 for oversize data, and 429 for quota refusal. No
token/lease hashes or other owners' jobs are included in owner listings.

## Evidence and integrity

A bundle contains exactly `manifest` and `trials`, not an archive. It must match
the reviewed canonical config, deterministic plan and immutable images, contain
all resolved trials with correct seeds/contracts, and report a stable engine.
Acceptance means structurally accepted evidence, **not that every trial passed**.
No assertion here proves that an authorized worker told the truth.

A receipt binds owner, job, attempt, worker, config digest, manifest digest, bundle
digest and acceptance time with HMAC-SHA256. Receipts are verified before serving
artifacts and during audit. Audit rows form a separate ordered HMAC chain. This is
symmetric integrity, **not a publicly verifiable signature, remote attestation,
or protection from an administrator who controls both DB and key**. Preserve an
external chain head to detect rollback; back up the DB and key consistently.

## Reproducible validation

`python3 -m scripts.validate_cluster --image sha256:<reviewed-image-ID> --trust-config`
starts a temporary local HTTP service, creates two owner jobs, runs two worker
identities sequentially through real Docker, checks cross-owner refusal and audit
integrity, and retains local run directories plus a private `validation.json`.
It prints paths and counts, never credential values.

See [M5/M6 execution evidence](m5-m6-validation.md) and the named regression suites.
