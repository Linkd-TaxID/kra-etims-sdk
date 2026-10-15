#!/usr/bin/env bash
set -euo pipefail

destination="${1:?Usage: install-gitleaks.sh /absolute/path/to/gitleaks}"
case "$destination" in
  /*) ;;
  *) echo "Gitleaks destination must be absolute" >&2; exit 2 ;;
esac
archive_dir="$(mktemp -d)"
trap 'rm -rf "$archive_dir"' EXIT
curl --fail --location --retry 2 \
  https://github.com/gitleaks/gitleaks/releases/download/v8.30.1/gitleaks_8.30.1_linux_x64.tar.gz \
  --output "$archive_dir/gitleaks.tar.gz"
echo "551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb  $archive_dir/gitleaks.tar.gz" | sha256sum --check
tar -xzf "$archive_dir/gitleaks.tar.gz" -C "$archive_dir" gitleaks
install -m 0755 "$archive_dir/gitleaks" "$destination"
