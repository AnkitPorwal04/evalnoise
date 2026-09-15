# Release readiness: public source, local preview

EvalNoise 0.6 is a public source repository with a local development preview, not a publicly supported
service. Public source visibility does not deploy the demo or coordinator.
The source is licensed under the [MIT License](../LICENSE). No package has been uploaded to PyPI.
An installation check is not a security certification or a completed research gate.

## Clean installation

From a source checkout with Python 3.11 or newer:

```sh
python -m scripts.check_install
```

This builds an isolated wheel (build dependencies require network access), creates
a fresh virtual environment, installs that wheel with no runtime dependency
downloads, and changes to a temporary directory outside the checkout. It exercises
the installed `evalnoise --help` entry point and serves the packaged workbench,
checking its HTML, JavaScript and CSS over loopback HTTP. It does not invoke Docker,
read model credentials, or create a model request. The temporary installation is
removed on exit. CI repeats this test on the supported Python 3.11 floor.

For normal installation, run `python -m pip install .` from the checkout, then
`evalnoise --help`. The wheel includes the measurement/analysis commands and the
workbench asset. Workload Dockerfiles, study scripts and research documents remain
source-checkout resources: installing the wheel does not install or build images.
Run `evalnoise doctor` before a Docker experiment; do not interpret a missing image
or unavailable engine as a workload failure. Setup does not automatically retry,
download experiment images, or select a paid provider.

## Locally prepared static demonstration

```sh
python -m scripts.static_demo RUN_DIRECTORY --output runs/my-static-demo
```

Replace `RUN_DIRECTORY` with the path printed by your own demonstration run;
follow the [README walkthrough](../README.md#reproduce-the-48-trial-demonstration).
The exporter requires all 48 unique cells of the
reviewed four-task/four-profile/three-repeat design. It refuses unfamiliar outcome
categories and arbitrary labels rather than silently hiding them. A new output
directory is mandatory. Raw evidence is never rewritten.

Output consists of `index.html` and `evidence.json`. There is no script, external
font, analytics, login, worker API, coordinator link, or network dependency. The
projection copies only fixed labels and typed observation fields; it does not
copy arbitrary metadata, raw IDs, logs, host paths, or credentials. A canonical
source digest links to the private evidence without distributing that evidence.
This is not a general-purpose redactor, signed receipt, or remote attestation.
Review both output files before any future publication. A label or outcome outside
the reviewed vocabulary requires an explicit exporter review, not deletion of the
observation. No public deployment is part of this step.

## Readiness boundaries

- **Implemented and exercised:** local Docker experiments, independent verifiers,
  raw evidence, descriptive comparisons, read-only investigation, packaged CLI.
- **Local pilot only:** authenticated coordinator, trusted workers, leases and
  HMAC acceptance receipts. Do not expose it as a public multi-tenant service.
- **Experimental/research:** fixed-horizon block analysis and subscription preflight.
  The subscription command currently blocks before a model call on the installed
  CLI; it is not a working live model adapter.
- **Still open:** M3 live-provider validation, broader M4 statistical review,
  hostile-worker isolation, production TLS/HA/key rotation,
  wider-deployment review and support policy.

## Verification record

The clean-wheel check passed locally on Python 3.14, including installed assets
served outside the source directory. The generated local preview contains all 48
observations. Chromium checks at 1440 and 390 pixels found no page overflow or
JavaScript errors, including the expanded evidence table and JSON download.
Three exporter tests cover secret-bearing metadata exclusion, arbitrary-label
refusal, numeric validation, missing/duplicate cells and typed execution evidence.

CI now pins the verified Node 24 releases of `actions/checkout` (v7.0.1) and
`actions/setup-python` (v7.0.0) by full commit SHA. Existing Python, Docker and
browser checks remain; the clean-install check is an additional job. Consult the
Actions run for the current commit rather than treating an earlier passing run
as verification of later changes.
