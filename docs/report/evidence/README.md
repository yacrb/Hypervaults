# Recorded security and demo evidence

This folder preserves the evidence supporting the [illustrated report](../HyperVaults_Report.pdf).
Results describe the tested report snapshot. They are not live CI status or a
claim of production acceptance; subsequent code changes should be checked again.

| Evidence | What it records |
|---|---|
| [Python API tests](api-tests.txt) | 54 passing checks of identity, MFA, sessions, access, encryption and setup guards. |
| [Contract tests](contract-tests.txt) | 16 passing cases, including 512 randomized reward inputs. |
| [Browser demo](browser-demo.txt) / [structured results](demo-results.json) | Real registration-to-withdrawal journey, four isolated transactions and no page errors. |
| [MFA browser check](mfa-browser-tests.txt) | Independent QR decoding at four widths and real enrollment/sign-in while chain configuration is offline. |
| [Auth UI checks](auth-browser-tests.txt) / [account UI checks](account-browser-tests.txt) | Focused browser regressions and responsive controls. |
| [Dependency audit](dependency-audit.json) | 61 runtime packages, with no known advisories in that recorded scan. |
| [Python coverage](coverage.txt) / [Solidity coverage](contract-coverage.txt) | Diagnostics, including the contract instrumentation warnings; coverage alone does not establish security. |
| [Live Sepolia checks](live-sepolia.txt) | Website sign-in/out, security APIs and read-only contract state with three offers and covered reserves. |
| [Report quality checks](report-qa.txt) | PDF structure, authors, visual review and the report checksum. |

The numbered PNGs are all 18 original captures from the disposable browser demo.
The test harness used a separate Anvil container, temporary database, real API,
compiled contracts and a wallet bridge to that node's unlocked accounts. It
deleted that chain and those accounts afterward. Displayed QR/recovery codes
belong to that deleted test account, not the running Sepolia app.

Live Sepolia financial transactions were not sent for this report. Physical wallet
extensions, phone scanning, real SMTP delivery, public HTTPS, backup restore and
production load were not validated. MFA remains optional and email ownership
verification is absent. See [reproduction instructions](../README.md) and the
report's remaining improvements for the full scope.
