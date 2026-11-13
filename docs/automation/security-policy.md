# Security policy

Gitleaks runs on PR and default branch CI and against repository history in the Security monitor. The binary is pinned by version and SHA256. The workflow uses full redaction and avoids uploading reports. Follow [private reporting](../../SECURITY.md) for the underlying evidence.

Dependabot updates GitHub Actions references on a weekly schedule. The security workflow polls the alert API daily and reconciles existing findings. Full history scans run on Mondays and when manually dispatched. Cppcheck and clang tidy report code diagnostics in CI. Treat those diagnostics as review inputs. Projects with additional package ecosystems should register their manifests in Dependabot once they exist. GoogleTest is pinned to a Git commit and requires manual review because CMake FetchContent is outside Dependabot update coverage.

| Suggested severity | CVSS score |
| --- | --- |
| Critical | 9.0 to 10.0 |
| High | 7.0 to 8.9 |
| Medium | 4.0 to 6.9 |
| Low | Above 0 and below 4.0 |
| Informational | 0, or no severity information |

The normalizer uses an available advisory CVSS score, then scanner severity. Secret patterns default to Medium pending validation. Reviewers adjust severity based on exploitability, reachability, machine safety and data exposure. A matched Dependabot advisory confirms a dependency advisory match. Product exploitability requires separate validation.

A validated Critical or High finding alerts Telegram when ingested. GitHub sends native Dependabot notifications between daily runs. Open the Security tab to review evidence or dispatch Security monitor for immediate intake. On public CK-lab, a finding issue contains the opaque ID, scanner, suggested severity, owner role and state. Review package and CVE details in Dependabot and secret evidence through a private local scan. Assign an owner in GitHub and record decisions through the private triage channel. Synthetic examples only emit a classification in the log.

GitHub's public repository secret scanning and push protection supplement Gitleaks. The setup uses no paid Code Security or Secret Protection feature. See [GitHub security settings](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/enabling-features-for-your-repository/managing-security-and-analysis-settings-for-your-repository).
