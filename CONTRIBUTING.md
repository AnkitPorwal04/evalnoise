# Contributing

Start with [the product requirements](docs/product.md), [methodology](docs/methodology.md), and [security boundary](docs/security.md). Small changes should strengthen a named invariant; larger features need an architecture decision and acceptance gate.

## Development

Use Python 3.11+ and run `python3 -m unittest discover -s tests -v`. Runtime code currently uses only the standard library. New dependencies need a concrete benefit and review of operational and supply-chain cost. For Docker changes, build the fixture and run the opt-in integration suite.

## Change Requirements

- Pair behavioral changes with regression tests.
- Keep subprocesses argv-only and cleanup scoped to the exact owned run.
- Preserve raw evidence and missingness; never convert unavailable metrics into zero.
- Update the data contract if fields or meanings change.
- Label mocks, fixtures, statistical assumptions, and untested environments accurately.
- Keep credentials, generated runs, private datasets, and employer material out of source control.
- Do not publish benchmark claims without a reviewable experiment and stated limitations.

## Review

Review correctness, resource lifecycle, trust boundaries, artifact compatibility, and interpretation separately. A UI-only change can still change the apparent denominator or hide a failure. An instrumentation change can alter the measured workload. Tests passing is necessary, not sufficient, for methodological validity.

No license is asserted by this scaffold. The repository owner should choose licensing deliberately before presenting it as a licensed open-source release.
