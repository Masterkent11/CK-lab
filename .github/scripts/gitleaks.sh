#!/usr/bin/env bash
set -euo pipefail

report=$1
range=${2:-}
version=8.30.1
archive="gitleaks_${version}_linux_x64.tar.gz"
digest=551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb
tool=$(mktemp -d)
trap 'rm -f -- "$tool/gitleaks" "$tool/$archive" "$tool/diagnostics"; rmdir -- "$tool"' EXIT
curl --silent --show-error --fail --location --retry 3 --max-time 45 \
  "https://github.com/gitleaks/gitleaks/releases/download/v$version/$archive" --output "$tool/$archive"
printf '%s  %s\n' "$digest" "$tool/$archive" | sha256sum --check --status
tar -xzf "$tool/$archive" -C "$tool" gitleaks
args=()
if [[ -n "$range" ]]; then args+=("--log-opts=$range"); fi
code=0
"$tool/gitleaks" git --redact=100 --no-banner --exit-code 25 --report-format json --report-path "$report" \
  --timeout 180 "${args[@]}" . > "$tool/diagnostics" 2>&1 || code=$?
case "$code" in
  0) printf '%s\n' 'Gitleaks completed with no matches.' ;;
  25) printf '%s\n' 'Gitleaks detected candidate secrets. Review scanner evidence in private.' ;;
  *) printf 'Gitleaks did not complete (exit %s).\n' "$code" >&2 ;;
esac
exit "$code"
