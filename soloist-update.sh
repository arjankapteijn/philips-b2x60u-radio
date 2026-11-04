#!/bin/bash
# soloist-update - install the newest Spotify Soloist build if it differs from
# the installed one, then restart Soloist. Builds stop working 90 days after
# their build date (exit code 10), so soloist-update.timer runs this weekly.
# Spotify's terms: download from Spotify, never redistribute the binary.

set -euo pipefail

case "$(uname -m)" in
    aarch64) ARCH=arm64 ;;
    armv7l) ARCH=arm32 ;;
    x86_64) ARCH=x86_64 ;;
    *) echo "unsupported architecture $(uname -m)"; exit 1 ;;
esac

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
curl -sSfL -o "$TMP/soloist.tar.gz" "https://soloist-builds.spotifycdn.com/soloist_release_$ARCH.tar.gz"
tar -xzf "$TMP/soloist.tar.gz" -C "$TMP" soloist
new=$("$TMP/soloist" --version)
old=$(/usr/local/bin/soloist --version 2>/dev/null || echo "none")
if [ "$new" = "$old" ]; then
    echo "up to date: $old"
    exit 0
fi
sudo -n install -m 755 "$TMP/soloist" /usr/local/bin/soloist
echo "updated: $old -> $new"
systemctl --user reset-failed soloist.service 2>/dev/null || true
systemctl --user restart soloist.service
