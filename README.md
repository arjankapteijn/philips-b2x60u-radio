# Philips B2X60U Spotify radio

A 1956 Philips B2X60U tube radio turned into a Spotify Connect speaker with a Raspberry Pi inside. The original knobs still work: volume and play/pause on the left, tracks and favourite playlists on the right. There is no screen, no menu and no internet radio, just two pieces:

- **Spotify Soloist**, Spotify's own Connect speaker for Linux, playing through PipeWire
- **`radio_knobs.py`**, a small Python service that reads the knobs (libgpiod, no polling) and talks to Soloist over its local WebSocket API

It runs on a Pi 4 with Raspberry Pi OS Lite (trixie) and should also fit a Pi Zero 2 W.

## Controls

| | Turn | Press | Hold 3 s |
|---|---|---|---|
| **Left** | volume | play / pause | shut down the Pi |
| **Right** | previous / next track | next favourite playlist | add the current playlist to favourites |

- The volume stays in sync with the Spotify app, in both directions.
- Pressing left while nothing is playing (for example after boot) starts the last favourite, so you don't need a phone.
- **LED:** on while Spotify plays with volume > 0. It blinks twice when switching favourites, three times when one is added, and quickly while shutting down.
- **Power on after shutdown:** wire the left push button to **GPIO3** (physical pin 5) and set `LEFT_BUTTON=3`. The Pi firmware wakes on that pin.

## Hardware

| Part | Connection (BCM) |
|---|---|
| Raspberry Pi 4 Model B | |
| HiFiBerry MiniAmp (DAC + 2 × 3 W, I2S) | GPIO18–21, `dtoverlay=hifiberry-dac` |
| Left knob: rotary encoder (PEC11) + push button | GPIO14, GPIO15, button GPIO4 |
| Right knob: rotary encoder + push button | GPIO23, GPIO24, button GPIO17 |
| Status LED | GPIO26 |
| Speaker: 20 W, 4 Ω full range | one MiniAmp channel |

- **GPIO26 is shared:** the MiniAmp uses it to switch its power stage ([HiFiBerry GPIO usage](https://hifiberry.com/?p=64904)), and high means on. So the LED doubles as the amplifier switch: when playback stops, the amplifier goes off too and there is no hum. If radio-knobs isn't running, the radio stays silent.
- **Mono:** there is one speaker, so PipeWire mixes left and right to (L+R)/2 on both channels. If you add a second speaker, connect it to the other channel (never two on one channel) and start Soloist with `--pipewire-device dac` for stereo.
- The serial console must stay off (`enable_uart=0`), because GPIO14/15 carry the left knob.

## Audio

```
Soloist ─▶ radio  (mono sink "Philips B2X60U", mixes (L+R)/2)
              └─▶ dac  (HiFiBerry, hw:CARD=sndrpihifiberry, both channels)
```

Everything runs in PipeWire at a fixed 44.1 kHz, the rate Spotify streams at, so there is no resampling and no ALSA config. WirePlumber's card detection is turned off, which leaves these two sinks as the only ones. Soloist does the volume control. PipeWire and the DAC stay at 100 %.

Soloist, PipeWire and the update timer run as **user services** of `pi`, and `loginctl enable-linger pi` starts them at boot. radio-knobs is a system service. It reconnects to Soloist's WebSocket (`127.0.0.1:3679`) whenever Soloist restarts.

## Install

1. Use Raspberry Pi Imager to write **Raspberry Pi OS Lite (64-bit)**, with user `pi`, SSH and Wi-Fi enabled.
2. On the Pi:
   ```bash
   sudo apt install -y git
   git clone https://github.com/arjankapteijn/philips-b2x60u-radio.git radio
   cd radio && ./install.sh
   ```
3. Create a Soloist API key in the [Spotify for Developers](https://developer.spotify.com/dashboard) dashboard (Premium account required), then store it on the Pi:
   ```bash
   umask 077; mkdir -p ~/.config/soloist
   echo 'SOLOIST_API_KEY=your-key' > ~/.config/soloist/soloist.env
   sudo reboot
   ```
4. In the Spotify app, pick **Philips B2X60U** once. After that, Soloist remembers the login.

`install.sh` is safe to run again. It never overwrites an existing `/etc/radio-knobs.conf` or favourites list.

## Configuration

- **`/etc/radio-knobs.conf`** holds the pins, `LEFT_INVERT` / `RIGHT_INVERT` (set these if a knob turns the wrong way), `VOLUME_STEP`, `HOLD_TIME` and `SHUFFLE` (favourites start shuffled, default `yes`). Run `sudo systemctl restart radio-knobs` after a change.
- **`/var/lib/radio-knobs/favourites`** has one playlist per line (`spotify:playlist:…` or a share link). The easiest way to fill it is to play a playlist and hold the right knob for 3 seconds.

## Things to know about Soloist

- **The key stays out of git.** It lives only in `~/.config/soloist/soloist.env` (mode 600). Soloist takes the key as a command-line argument, so `ps` shows it to local users.
- **Builds expire.** Each Soloist build stops working 90 days after its build date (exit code 10). `soloist-update.timer` runs every Monday night: it downloads the latest build from Spotify and restarts Soloist when the version changes. Spotify's terms forbid redistributing the binary, so it isn't in this repo.
- **Clock sync:** the Pi has no battery-backed clock. With a clock that is far off, Spotify rejects the login (`login failed: make sure --api-key is valid`) and Soloist doesn't retry. To prevent that, `soloist.service` waits up to 3 minutes for timesyncd before it starts. The knobs do nothing until then. `timesyncd-cloudflare.conf` speeds this up: timesyncd tries once at ~11 s, before WiFi is up, and is not told when NetworkManager brings the network up, so it waited its default 30 s `ConnectionRetrySec` (sync at ~42–50 s). The drop-in sets that to 3 s and uses `time.cloudflare.com` (answers in under 10 ms; the Debian pool once timed out over IPv6).

```bash
soloist ctl status
journalctl --user -u soloist -f
journalctl -u radio-knobs -f
```

## Files

| File | Installed as |
|---|---|
| `radio_knobs.py` | `/usr/local/bin/radio-knobs` |
| `radio-knobs.conf` | `/etc/radio-knobs.conf` |
| `radio-knobs.service` | `/etc/systemd/system/` |
| `favourites` | `/var/lib/radio-knobs/favourites` (example) |
| `soloist.service` | `~/.config/systemd/user/` |
| `soloist-update.sh` | `/usr/local/bin/soloist-update` |
| `soloist-update.service`, `.timer` | `~/.config/systemd/user/` |
| `pipewire-radio.conf` | `/etc/pipewire/pipewire.conf.d/radio.conf` |
| `wireplumber-radio.conf` | `/etc/wireplumber/wireplumber.conf.d/radio.conf` |
| `install.sh` | installs all of the above |
| `sync-from-pi.sh` | copies the live files from the radio back into the repo (no key, login or favourites) |
