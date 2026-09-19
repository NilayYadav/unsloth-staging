PR #11297 controlled resume-progress evidence

Source: https://github.com/unslothai/unsloth/pull/11297
Before: ef00d4946731d2cb9bed2982f1a24346843e7e19 (merge base)
After: 6d62eb4cf9066a0ec78410a31b69d0a5c9208462

Modal Linux, Chromium 153, viewport 1366x900. Identical controlled training state: resume at 900/1000, then 10 optimizer steps in 60 seconds. Real shipped callbacks, worker progress events, backend event handler, SSE route, frontend parser, runtime store and LiveTrainingView execute. Heavy model imports and unrelated UI APIs are stubbed. This proves the changed progress path; it does not claim a GPU training run or a History-click checkpoint lifecycle test.

Before visible ETA 5s, throughput 15.17 steps/s. After visible ETA 9m 0s, throughput 0.17 steps/s. Both retain step 910/1000, elapsed 60s and 91% overall progress. Browser page errors: 0. Before fails the correct-ETA assertion; after passes. Composite visually inspected; image bytes and numerical facts differ as expected.

An additional 600-second setup-delay scenario detects the review findings: the original PR head emits 5940s ETA; fixed callbacks emit 540s. Deterministic regression tests in Modal: 2 failed/29 passed before the timer fix; 31 passed after. Three focused frontend tests and TypeScript checks pass.

Only sanitized numerical facts and the reviewed screenshot are published here.
