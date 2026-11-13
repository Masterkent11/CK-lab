# Operations runbook

## Schedule

The requested times are 9:00 AM Manila on weekdays for the digest and 5:00 PM on Friday for the weekly summary. The workflow starts at seven minutes past the hour to reduce congestion. Sprint start and end share the 9:07 AM daily trigger and are calculated from the configured baseline. Dependabot intake runs daily at 9:23 AM and on demand. Full history scanning runs on Mondays and on demand. Update the workflow cron and matching configuration window together if timings change. Delivery is best effort. GitHub can delay or drop scheduled jobs.

## Smoke checks

1. Run `python -m unittest discover -s tests -v`. The integration test exercises seed, Project item fields, draft scope, explicit approval and a repeated notification with mock APIs.
2. Run Engineering operations with `sprint_start` or reproduce the Sprint 0 report locally with `python -m automation --date 2026-11-16 sprint_start`.
3. Run `classify-fixture`. Verify `synthetic: true` and that the issue list is unchanged.
4. With Project access, run `setup` then `seed` with Apply. Confirm the UX prototype issue appears under UX/UI Design Track with three proposed points and Backlog status. Rerun seed and verify the original issue and item IDs.
5. With Telegram configured, run `test-notification` with Apply. Rerun the same Actions run and confirm there is one message. Check the delivery log before retrying an uncertain send.
6. Use an authorized test PR with an explicit issue link to verify review, CI and QA transitions. Check failed Actions jobs for the first failing step and the linked issue for the failure note.

## Pause and recover

Set `AUTOMATION_WRITES` to `false` to pause GitHub and Telegram mutations. Reports and CI can still run. Cancel any in flight write job if necessary. After restoring access, rerun the failed workflow or use `reconcile` with affected issue numbers. Use native Projects editing to undo a mistaken sprint assignment. Keep the delivery issue and managed issue markers. The receipt ledger is capped at 220 entries and 90 days. Confirm old deliveries manually before replaying an older job.

## Local build

```sh
cmake -S . -B build -DCMAKE_BUILD_TYPE=Debug
cmake --build build
ctest --test-dir build --output-on-failure --no-tests=error
```

Use `-DEMBROIDERY_SANITIZERS=ON` on Linux with Clang or GCC. Add new translation units to the relevant CMake target so clang tidy can use its compile database.

## Implementation status

| Status | Scope |
| --- | --- |
| Implemented and verified | Automation unit tests, mocked Project and Telegram integration, hosted CI, repository backlog seeding and rerun, PR and QA lifecycle, Gitleaks, Dependabot alert API |
| Implemented but not externally verified | Project provisioning mutations, Roadmap presentation, delivery cooldowns against the real Telegram API |
| Blocked by permissions or credentials | Live personal Project access and Telegram delivery until the repository secrets are configured |
| Deferred | Embroidery algorithms, mobile features and optional local AI |

## Baseline runs

* [Engineering CI](https://github.com/Masterkent11/CK-lab/actions/runs/43480051392) completed the Python tests, C++20 build, GoogleTest via CTest, format and static analysis, sanitizer tests and Gitleaks.
* [Seed run](https://github.com/Masterkent11/CK-lab/actions/runs/43480138728) created the backlog. The [UX prototype](https://github.com/Masterkent11/CK-lab/issues/13) is a proposed story. The [CI task](https://github.com/Masterkent11/CK-lab/issues/18) has a native dependency on the CMake task.
* [Security monitor](https://github.com/Masterkent11/CK-lab/actions/runs/43481275789) completed Gitleaks and Dependabot alert intake with zero findings.
* [Seed rerun](https://github.com/Masterkent11/CK-lab/actions/runs/43480569423) retained the same sixteen managed issues.
* [Sprint 0 preview](https://github.com/Masterkent11/CK-lab/actions/runs/43480577760) generated a draft and found one blocked ticket. [Synthetic classification](https://github.com/Masterkent11/CK-lab/actions/runs/43480573020) logged a High sample without creating a finding.
* [Hosted lifecycle test](verification.md) records the controlled CI failure, recovery and QA acceptance.
