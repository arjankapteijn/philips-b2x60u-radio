#!/bin/bash
# install.sh - set up the Philips B2X60U as a Spotify-only radio on a fresh
# Raspberry Pi OS Lite (trixie), Pi 4 or Pi Zero 2 W. Run as user pi from this
# directory: ./install.sh   (safe to run again)
#
# Spotify Soloist needs your own API key (Spotify for Developers dashboard,
# Premium account). Put it in ~/.config/soloist/soloist.env as
# SOLOIST_API_KEY=... before or after running this; it is never in this repo.

set -euo pipefail
cd "$(dirname "$0")"

CONFIG_TXT=/boot/firmware/config.txt
ENV_FILE=~/.config/soloist/soloist.env

echo "== Packages"
sudo apt-get update -q
sudo apt-get install -y -q --no-install-recommends python3-libgpiod python3-websocket curl
# PipeWire for Soloist, nothing more: no pipewire-alsa or pipewire-pulse, as
# Soloist talks PipeWire directly
sudo apt-get install -y -q --no-install-recommends pipewire wireplumber

echo "== HiFiBerry DAC in $CONFIG_TXT"
sudo sed -i -e 's/^dtparam=audio=on/#dtparam=audio=on/' "$CONFIG_TXT"
grep -q '^dtparam=audio=off' "$CONFIG_TXT" || echo 'dtparam=audio=off' | sudo tee -a "$CONFIG_TXT" >/dev/null
grep -q '^dtoverlay=hifiberry-dac' "$CONFIG_TXT" || echo 'dtoverlay=hifiberry-dac' | sudo tee -a "$CONFIG_TXT" >/dev/null
# GPIO14/15 carry the volume knob, so the serial port must stay off
if grep -q '^enable_uart=1' "$CONFIG_TXT"; then
    echo "WARNING: enable_uart=1 in $CONFIG_TXT uses GPIO14/15 (left knob); remove it"
fi

echo "== PipeWire sinks (mono mix to the DAC)"
sudo install -D -m 644 pipewire-radio.conf /etc/pipewire/pipewire.conf.d/radio.conf
sudo install -D -m 644 wireplumber-radio.conf /etc/wireplumber/wireplumber.conf.d/radio.conf

echo "== Spotify Soloist"
sudo install -m 755 soloist-update.sh /usr/local/bin/soloist-update
install -d ~/.config/systemd/user
install -m 644 soloist.service soloist-update.service soloist-update.timer ~/.config/systemd/user/
# Soloist and PipeWire are user services of pi; linger starts them at boot
sudo loginctl enable-linger pi
export XDG_RUNTIME_DIR=/run/user/$(id -u)
systemctl --user daemon-reload
[ -x /usr/local/bin/soloist ] || soloist-update
systemctl --user enable soloist.service soloist-update.timer

echo "== radio-knobs"
sudo install -m 755 radio_knobs.py /usr/local/bin/radio-knobs
[ -e /etc/radio-knobs.conf ] || sudo install -m 644 radio-knobs.conf /etc/radio-knobs.conf
sudo install -d -o pi -g pi /var/lib/radio-knobs
[ -e /var/lib/radio-knobs/favourites ] || sudo install -m 644 -o pi -g pi favourites /var/lib/radio-knobs/favourites
sudo install -m 644 radio-knobs.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable radio-knobs.service

echo
echo "Done. Reboot to load the HiFiBerry overlay: sudo reboot"
if [ ! -s "$ENV_FILE" ]; then
    echo "Soloist will not start until $ENV_FILE contains SOLOIST_API_KEY=... (chmod 600)."
fi
echo "Then pick 'Philips B2X60U' once in the Spotify app; Soloist remembers the login."
