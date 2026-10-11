# Illustrated security report

The report explains the actual HyperVaults architecture, website authentication,
wallet linking, MFA, encrypted notes, authorization and contract safeguards. Its
purple cover uses the site's artwork and the two authors, without a class label,
module name or cover date. Nine diagrams remain editable SVGs. Demo images are real
browser captures; screenshots are never substituted with drawings or mockups.

The published snapshot is available directly in the repository:
[23-page PDF](HyperVaults_Report.pdf), [editable diagrams](../assets/security),
and [original screenshots and test evidence](evidence/README.md).

Generated PDF, assets and evidence live in `output/pdf/hypervaults-report/` and
are ignored by Git. The checked-in scripts describe this verified report snapshot;
if the app or test counts change, update its prose and assertions before publishing.

## Verified scope

- 54 Python API/algorithm checks and 16 Solidity checks passed, including 512
  randomized reward calculations.
- The isolated browser journey completed registration, wallet connection/linking,
  faucet, exact approval, deposit, encrypted note, matured withdrawal while paused,
  QR enrollment, email MFA, wallet MFA with a recovery code and administrator views.
- Four genuine disposable-chain transaction receipts, no browser page errors and
  20 responsive checks across five pages and four viewport widths were recorded.
- Independent QR decoding passed at 1280, 768, 375 and 320 pixels, with the decoded
  secret successfully enabling MFA through the real browser and API.
- A pinned runtime dependency audit checked 61 packages with no known advisories.
- The existing Sepolia app passed health, sign-in/out, CSRF, account-security and
  read-only contract checks: three offers with covered reserves.

The browser harness runs the real application against a uniquely named MongoDB
database and a newly created isolated Anvil container. Its injected wallet bridge
uses that node's unlocked accounts. It does not use a real wallet private key,
modify `.env` or deployment manifests, reset a saved chain, or alter existing users.
Cleanup removes only the harness's own container, server and database. QR secrets
and recovery codes in screenshots belong to the deleted disposable account.

This is scoped test evidence, not an independent audit or production acceptance.
Physical wallet extensions and phone scanning, real SMTP delivery, public HTTPS,
backup restore and production load were not validated. MFA is optional, email
ownership verification is absent and HVT is an unpriced test token. The report
records these gaps instead of treating passing checks as universal assurance.

## Reproduce the evidence

Run from the repository root with Python 3.12+, `uv`, Docker, the pinned Foundry
toolchain, local MongoDB on port 27018 and Playwright Chromium available. Use the
usual project toolchain installation; never rebuild a saved deployment merely to
produce the report. Compile contracts before the browser financial journey.

```sh
mkdir -p output/pdf/hypervaults-report/evidence .local/report-work
HV_AUTH_TEST_MONGO_URI=mongodb://127.0.0.1:27018 \
  uv run --with coverage coverage run --branch -m unittest discover -s tests -v \
  > output/pdf/hypervaults-report/evidence/api-tests.txt 2>&1
.tools/bin/forge test --root smartcontract --fuzz-runs 512 -vv \
  > output/pdf/hypervaults-report/evidence/contract-tests.txt 2>&1
HV_AUTH_TEST_MONGO_URI=mongodb://127.0.0.1:27018 \
HV_REPORT_EVIDENCE_DIR=output/pdf/hypervaults-report/evidence \
  uv run --with playwright --with coverage coverage run --append --branch \
  -m tests.check_app_e2e \
  > output/pdf/hypervaults-report/evidence/browser-demo.txt 2>&1
HV_AUTH_TEST_MONGO_URI=mongodb://127.0.0.1:27018 \
  uv run --with playwright python -m tests.check_mfa_e2e \
  > output/pdf/hypervaults-report/evidence/mfa-browser-tests.txt 2>&1
uv run --with playwright python tests/check_auth_ui.py \
  > output/pdf/hypervaults-report/evidence/auth-browser-tests.txt 2>&1
uv run --with playwright python tests/check_account_ui.py \
  > output/pdf/hypervaults-report/evidence/account-browser-tests.txt 2>&1
uv run --with coverage coverage report \
  --include='auth/*,webapp/*,scripts/sepolia.py' \
  > output/pdf/hypervaults-report/evidence/coverage.txt
uv export --frozen --no-dev --no-emit-project --format requirements-txt \
  > .local/report-work/requirements.txt
uv run --with pip-audit pip-audit -r .local/report-work/requirements.txt \
  --disable-pip --no-deps --format json \
  --output output/pdf/hypervaults-report/evidence/dependency-audit.json
```

For an optional Solidity coverage diagnostic:

```sh
.tools/bin/forge coverage --root smartcontract --report summary \
  > output/pdf/hypervaults-report/evidence/contract-coverage.txt 2>&1
```

Coverage instrumentation reported anchor warnings. Preserve them alongside the
results; the separately asserted contract behavior suite is the stronger evidence.
Existing Sepolia checks are read-only apart from ordinary sign-in/out session
creation; use a demo identity locally and never include credentials in logs.

## Render and review

```sh
uv run --with playwright python docs/report/render_assets.py
```

`render_assets.py` writes the nine SVG diagrams, checks every card's text bounds
and renders them at twice their native resolution. It renders local technology
logos and the site's cover illustration in the same pass. `build_report.py` uses
ReportLab and Pillow to create 23 A4 pages with bookmarks, source links and explicit
page budgets. Image frames preserve the original screenshot pixels and aspect
ratios; the full original captures are included in the evidence bundle.

Use a Python runtime with ReportLab and Pillow, and Noto Sans / Rubik regular and
bold TTF files. The default fonts come from the desktop document runtime; another
installation can set `HV_REPORT_FONT_DIR` to their directory:

```sh
HV_REPORT_FONT_DIR=/path/to/fonts python docs/report/build_report.py
pdftoppm -r 120 -png output/pdf/hypervaults-report/HyperVaults_Report.pdf /tmp/hv-page
```

Inspect every rendered page for clipped text, unreadable labels, overlap and
incorrect captions. The authoring checks cannot replace visual review. Keep the
cover names exactly `Mohamed Yacine Riabi` and `Yassine ben lazreg`.
The PDF footer and page headers contain no report date or module name.

Share the PDF and an assets archive containing the editable diagrams, original
screenshots, raw test results and report source. Do not export the real database,
key ring, local configuration, wallet secrets or development recovery outbox.
