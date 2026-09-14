# M5 and local M6 validation record

Implementation and verification were performed directly in this session, without
implementation subagents. This record distinguishes a local coordinator pilot
from a production distributed deployment.

## M5

The persisted Chromium regression (`test_workbench_browser.py`) passed using
installed Google Chrome. It verifies manifest differences, a new trial appearing
under five-second refresh, filter and checkbox preservation, matching download
counts, escaped malicious log text, raw resource SVGs, 1440/390px layouts, and a
second run with reset filters. It found and fixed same-origin refresh being
blocked by CSP. External connections remain blocked.

## M6 real Docker observation

Private state: `cluster-state-9fb41e2ad24c` under ignored `runs/`.
Reviewed image: `sha256:d6ef9a0367233ca183876db4bb84f7ad48b32ccbbc9a03a75be66f2b0a07348d`.

- Alice/worker: `m6-owned-validation-111627997a71`, one passed CPU workload.
- Bob/worker-b: `m6-owned-validation-a0a26f2a86d8`, one passed CPU workload.
- Two accepted jobs with separate HMAC receipts; both cross-owner artifact reads
  refused. Audit chain and accepted artifact digests verified.
- Workers ran sequentially on the same local Docker Desktop engine. This is not
  evidence of multi-host throughput or adversarial tenant isolation.

The script leaves evidence intact, shuts down its temporary server, and prints no
credentials. Only paths, image identity, run IDs and aggregate observations are
documented here; state credentials, database and raw artifacts remain ignored.

## Failure and security regressions

`test_coordinator.py`: owner isolation and roles, idempotency, concurrent claims,
distinct workers, expired lease fencing, repeated completion, changed-result
refusal, cancellation/revocation, quota reservations, restart persistence, audit
and artifact tampering, pool mismatch and invalid bundles.

`test_cluster_http.py`: Host/origin/framing/JSON/size refusals; heartbeat loss
requests cooperative stop; a real claiming subprocess is killed, its lease
expires, another worker accepts exactly one result, and its old completion is
refused. That killed-process fixture does not start Docker. A separate opt-in
Docker test runs two owners through actual container execution and retrieval.

The first full local suite discovered 475 tests, executed 474 successfully, and
skipped the separately run browser test. It exposed three unclosed SQLite test
connections (context managers commit but do not close); the tests were corrected
to close explicitly. The final local suite discovered and executed 478 tests with
zero skips or failures in 76.164 seconds, with both Docker and browser opt-ins
enabled. No thread tracebacks or SQLite resource warnings appeared.

Python compilation, JavaScript syntax checking, and wheel construction passed.
Python language-server diagnostics were clear on the new modules and tests.
The JavaScript language server was unavailable; syntax and browser checks were
used instead. Remote CI is recorded separately after publication.

## Not claimed

No model/provider call, subscription use or paid API. No Internet deployment,
public-key signing, HA/failover, production SLO, or hostile multi-tenant security
certification. No power to stop a disconnected worker instantaneously. M3's live
provider gate and broader M4 methodology gates remain open.
