# Setup

The workflows target the existing `master` default branch of CK-lab. Update the CI push branch in `engineering-ci.yml` if the default branch changes.

## Repository settings

1. Enable Issues and GitHub Actions. Use the restricted default workflow token and leave PR creation by Actions disabled. Individual jobs declare the permissions they need.
2. Set the repository Actions variable `AUTOMATION_WRITES` to `true` to allow writes. The manual workflows also require the Apply input.
3. Run Engineering operations with `setup` and Apply selected to initialize repository labels.
4. Run `seed` and Apply to create the ten epics and six Sprint 0 proposals. The seed links subissues to epics and records the CI task dependency.
5. Confirm Dependabot alerts and private vulnerability reporting in Settings. GitHub provides [Dependabot alerts](https://docs.github.com/en/code-security/how-tos/secure-your-supply-chain/secure-your-dependencies/configure-dependabot-alerts) and [private vulnerability reporting](https://docs.github.com/en/code-security/how-tos/report-and-fix-vulnerabilities/configure-vulnerability-reporting/configure-for-a-repository).

## Project access

CK-lab is owned by a personal account. Create a classic token with the `project` scope under an account with write access to the intended Project. This scope includes `read:project`. The existing public repo can be read with the separate repository token. Add `repo` to the Project credential if it must access private repository content. For an organization Project, an installed GitHub App can use organization Projects read and write plus repository metadata and issue read access on the linked repository. Store the resulting Project credential as `PROJECT_TOKEN`. App installation tokens expire, so an App setup needs a token minting step before use. See [GitHub Projects API permissions](https://docs.github.com/en/issues/planning-and-tracking-with-projects/automating-your-project/using-the-api-to-manage-projects) and [GitHub token management](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens).

Set `ENGINEERING_PROJECT_NUMBER` to select an existing board. Without it, setup searches for the configured title and creates the board if needed. Run `setup` in preview mode first to review fields and views. Run again with Apply to provision. Once setup reports the Project URL, set the number and rerun `seed` to populate it using the existing issues. Use `reconcile` for other existing tickets.

Project setup preserves extra fields and options. It updates the layout and filter of the seven named views. Inspect the Roadmap view in GitHub and select Sprint as its date source if GitHub does not choose it. Review and disable any conflicting built in status workflows on the managed board, especially automatic Done on issue close. Native Assignees and Milestone show owners and target milestones. Use native issue dependencies for Blocked By.

## Other credentials

| Name | Location | Required access |
| --- | --- | --- |
| `GITHUB_TOKEN` | Provided by Actions. Export in memory for local use | Read repository and issue metadata for previews. Issues write for local setup and seeding |
| `PROJECT_TOKEN` | Repository Actions secret | Projects read and write and access to item content |
| `DEPENDABOT_TOKEN` | Optional repository Actions secret | Selected repository, Dependabot alerts read and Metadata read. A fine grained PAT suffices |
| `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` | Optional repository Actions secrets | Bot access to the chosen destination |

Use the secure settings forms or `gh secret set NAME` and its hidden prompt. Local commands read environment variables and default to preview. Use `python -m automation --help` to see the interface. Set `GITHUB_REPOSITORY=Masterkent11/CK-lab` in the local process.

## Cost controls

CK-lab is public. Standard hosted Actions runners are free for public repositories. GitHub currently describes private GitHub Free usage as 500 MB shared artifact storage and an included compute allowance. Its Actions billing page lists 2,000 standard Linux minutes and the included usage reference describes a monetary allowance. Check the account meter if repository visibility changes. This setup uses standard Linux runners, bounded job timeouts, no retained scanner artifacts and a weekly full scan. For private use, configure an Actions product budget with Stop usage enabled and verify the spending limit in Billing before enabling schedules. See [Actions billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions), [included usage](https://docs.github.com/en/billing/reference/product-usage-included) and [spending budgets](https://docs.github.com/en/billing/how-tos/set-up-budgets).
