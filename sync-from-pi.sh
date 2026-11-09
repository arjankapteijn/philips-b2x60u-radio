#!/bin/bash
# Copy the live files from the radio over the ones in this repo, then review
# with git diff. The Soloist API key and login (~pi/.config/soloist,
# ~pi/.local/share/soloist) and your favourites are not copied.

set -euo pipefail
cd "$(dirname "$0")"

HOST=${1:-pi@philips.local}

# repo file : path on the Pi
FILES="
radio_knobs.py:/usr/local/bin/radio-knobs
radio-knobs.conf:/etc/radio-knobs.conf
radio-knobs.service:/etc/systemd/system/radio-knobs.service
pipewire-radio.conf:/etc/pipewire/pipewire.conf.d/radio.conf
wireplumber-radio.conf:/etc/wireplumber/wireplumber.conf.d/radio.conf
timesyncd-cloudflare.conf:/etc/systemd/timesyncd.conf.d/10-cloudflare.conf
soloist.service:/home/pi/.config/systemd/user/soloist.service
soloist-update.sh:/usr/local/bin/soloist-update
soloist-update.service:/home/pi/.config/systemd/user/soloist-update.service
soloist-update.timer:/home/pi/.config/systemd/user/soloist-update.timer
"

for entry in $FILES; do
    ssh "$HOST" "cat '${entry#*:}'" > "${entry%%:*}"
done
echo "Synced from $HOST"
