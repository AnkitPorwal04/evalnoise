# M4 execution record

## First attempt

Command: `caffeinate -i -m -s python3 -m evalnoise run experiments/block-memory.json --trust-config --output runs`.
Exit 2 in preflight: `docker image failed (exit 1): Error response from daemon:
{"message":"No such image: evalnoise-workloads:local"}`. No trial began and no
run directory was allocated by the runner. Subsequent `docker image ls --digests
--no-trunc` showed the tag at
`sha256:d6ef9a0367233ca183876db4bb84f7ad48b32ccbbc9a03a75be66f2b0a07348d`.
`evalnoise doctor` succeeded on the same configured Docker Desktop endpoint,
Engine 29.1.5, API 1.52, Linux ARM64, cgroup v2. This is diagnostic evidence,
not a proven explanation of the earlier error.

A separate explicit fresh attempt follows. No automatic retry was added, no
trial was deleted, and no failed trial was changed to a pass.

The second explicit attempt failed identically before trials. A direct pinned
endpoint tag inspection also failed. Inspection by the listed immutable image ID
succeeded and reported ARM64. The study configuration was therefore changed
**before any trial** to that explicit image ID, without changing task code or
resource profiles. This bypasses the failing tag-resolution operation explicitly;
it is not a fix or explanation of Docker's tag lookup. The committed study config
is host-image-specific: other hosts must build the reviewed workload and specify
their own immutable image identity before starting a new study.

## Completed study

Run `block-memory-b3e1bc82febb`: 24 blocks, 3 fixed tasks, 2 profiles,
144/144 recorded trials, 72/72 matched task pairs. Baseline 48 MiB: 48 clean
passes and 24 `oom_killed`. Candidate 256 MiB: 72 clean passes. Every block's
candidate-minus-baseline clean-pass fraction was 1/3. No model was called.

Analysis command:
`python3 -m evalnoise block-analyze runs/block-memory-b3e1bc82febb --baseline tight --candidate roomy --treatment memory_mb --output runs/block-memory-analysis-b3e1bc82febb`.
Source evidence SHA-256:
`9f0e620044a1609c36e8e4748def83764757c298a41940e36da58d627c028051`.
At alpha 0.05, radius 0.5544426221 and clipped interval
[-0.2211092887, 0.8877759554] around the observed 0.3333333333 contrast.
This includes zero. It is not an LLM capability result or a causal estimate.
No trial/manifest was rewritten by analysis.

## Simulation and tests

The pre-specified seed-613 simulation (1,000 studies of 24 blocks per regime)
reported 5 misses for IID null, 2 for IID positive, 0 for heterogeneous independent
blocks, and 0 for the Markov regime. Coverage was respectively 99.5%, 99.8%,
100%, and 100% against the known average conditional expectation. All satisfied
the pre-specified alarm criterion. These deliberately conservative bounds are
not an empirical proof for arbitrary data, nor permission to target the
stationary mean under dependence.

Initial direct validation: six block tests passed; complete unit discovery
446 tests, 21 skipped, 425 executed and passed. Compileall succeeded and
`blocks.py` language-server diagnostics reported no errors. Existing Docker
integration tests were not rerun locally in that unit command; the 144-trial
study above was an actual, separate Docker execution.

Final local unit discovery after adding plan/seed-tampering and HTML-escaping
regressions: 448 discovered, 21 skipped, 427 executed and passed. All eight block
tests passed. Browser verification of the generated block report at 1440 and
390 pixels found no horizontal page overflow or JavaScript errors. This is a
single Chromium check, not cross-browser or accessibility certification.
