# Troubleshooting

| Symptom | Check |
| --- | --- |
| Actions workflow missing | Confirm the workflow is on the default branch and Actions is enabled |
| Run reports dry run | Confirm `AUTOMATION_WRITES=true` and select Apply on dispatch |
| Project skipped | Set `PROJECT_TOKEN`. Check the scope, board permissions and owner |
| GraphQL update fails | Run setup in preview and check the configured Project number, schema and named views |
| Item missing from a view | Check Project membership, view filters and Sprint value. Reconcile the issue number |
| Dependabot returns 403 or 404 | Check that alerts are enabled and the scanner credential has Dependabot alerts read access |
| Telegram returns 400 or 403 | Verify the destination and start the chat. Check the bot's group permissions |
| Digest is late | Check Actions history and the configured delivery window. Dispatch manually if a scheduled run was dropped |
| Duplicated events or rate limits | Check the operation logs and shared concurrency group. Rerun after the rate limit resets |
| Public scheduled runs stop | Check whether GitHub disabled schedules after 60 days of repository inactivity |

A successful job with `project_unconfigured`, `notification_unconfigured` or `scanner_unavailable` has a missing integration. Treat those log events as setup evidence.

Telegram transport failures can be ambiguous. The delivery ledger reserves a receipt before sending and leaves uncertain receipts suppressed. Verify the chat before clearing a receipt. Leave issue markers intact so the next run can reconcile prior writes.
