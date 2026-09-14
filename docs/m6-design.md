# M6 local coordination deployment contract

This milestone adds a separate authenticated coordinator and worker protocol.
The M5 evidence workbench remains read-only. Initial deployment binds loopback;
it is not an Internet-facing, hostile multi-tenant SaaS release. No provider or
subscription execution is allowed by the coordinator catalog.

## Trust and architecture decisions

1. A server administrator reviews fixed experiment configurations before startup.
   Each task and verifier uses an immutable local image ID. Owners submit a catalog
   ID, never shell commands, images, paths, or arbitrary experiment JSON. Workers
    independently restrict image IDs in their private local configuration. Pool
    membership is bound to worker identity in the coordinator, not a worker request.
2. Separate opaque 256-bit bearer credentials identify owners, workers, and an
   administrator. Only token hashes are stored in SQLite. Provisioned credential
   files are mode 0600 under a 0700 directory; no credentials belong in the repo.
   Authentication is distinct from owner-scoped object authorization.
3. SQLite WAL transactions reserve job/trial/storage quotas and acquire leases.
   Every claim increments an attempt and gets a new random lease capability.
   Heartbeats extend a live lease. Expiration permits a bounded retry, not silent
   replacement of historical run evidence. Submission idempotency is owner scoped.
4. Execution can occur **more than once**, acceptance is **at most once per job**. A stale
   worker cannot complete after lease expiry, reassignment, cancellation, or
   revocation. This is fencing, not a claim that a disconnected process cannot run.
   Workers stop their existing runner cooperatively after heartbeat failure.
5. Existing `execute(..., stop=...)` supplies pinned Docker identity, hardening,
   resource audit, same-engine advisory locking, cleanup, and run artifacts.
   Separate worker processes can target separately provisioned engines. Shared
   Docker daemons are not adversarial tenant isolation; use separate VMs/engines
   per trust domain before any wider deployment.
6. Completed bundles must contain the exact reviewed config and deterministic
   plan, all planned trials, consistent contract/image identity, and a stable
   engine. Validation is not remote attestation: a compromised trusted worker can
   fabricate observations. HMAC-SHA256 receipts authenticate coordinator-accepted
   manifest/bundle digests; they are symmetric MACs, **not public-key signatures**.
7. Append-only audit rows form an HMAC chain. It detects edits when checked with
   the retained signing key, not malicious rewriting by a server administrator
   who owns that key. Back up the DB and signing key together, protect backups,
   and retain an external chain-head checkpoint for rollback detection.
8. Explicit owner purge removes terminal bundle data but keeps job metadata and
   audit events. Enforced per-job and per-owner byte quotas bound retained payloads.
   No automatic destructive retention timer or public artifact URL is introduced.

## Incident response and limitations

Revoke a principal through the authenticated admin interface, cancel its active
jobs, stop the affected worker process, then use existing ownership-checked local
diagnosis/cleanup on retained run directories. Revocation prevents new requests
and completion; already running container code stops only when its worker receives
the heartbeat failure or local operator intervention. Never prune unrelated Docker
containers. Rotate credentials by reprovisioning identities; key rotation requires
retaining old signing keys for historical receipts (automatic rotation is deferred).

The clock is coordinator wall time; large clock jumps can expire leases early or
late. Deploy with synchronized time. SQLite uses a local filesystem, not NFS. A
single coordinator is the deployment boundary: no consensus/failover claim. TLS,
public-key signing, external object storage, multi-host network hardening, hostile
workload isolation, and production SLOs require a separate deployment review.

## Required validation

- Owners cannot enumerate/read/cancel/purge each other's jobs or bundles.
- Workers cannot submit jobs; owners cannot claim/heartbeat/complete.
- Concurrent claims, repeated completions, expired leases, lost workers,
  cancellations, revocations, restart persistence, and quota races are tested.
- Bad catalog/image/capability/config/plan/trial bundles are refused.
- Real local workers execute reviewed Docker tasks and preserve evidence. The
  killed-process test exercises a claimed lease without starting Docker; the
  real Docker test separately exercises the existing container lifecycle.
- Tampered receipts and audit rows fail verification; retention preserves audits.
- No credential or API-key fallback is passed to measured containers.
