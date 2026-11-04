#!/usr/bin/python3
# radio-knobs - the two knobs and the status LED of the Philips B2X60U
#
# Everything else is Spotify Soloist (Spotify Connect); this talks to its local
# WebSocket API, which also pushes every playback change, so nothing is polled.
# The knobs are read with libgpiod, which sleeps in the kernel until a pin
# changes: idle, this script uses no CPU.
#
#   Left knob   turn: volume            press: play/pause      hold: shut down
#   Right knob  turn: prev/next track   press: next favourite  hold: add the playing
#                                                                    playlist to favourites
#
# The status LED is on while Spotify plays with volume > 0. It blinks twice when
# switching favourites, three times when one is added and quickly while shutting
# down. With the left push button on GPIO3 a press also wakes the Pi after a
# shutdown (done by the Pi's firmware, not by this script).

import json
import logging
import subprocess
import threading
import time
from datetime import timedelta

import gpiod  # python3-libgpiod
import websocket  # python3-websocket
from gpiod.line import Bias, Direction, Edge, Value

CONFIG = "/etc/radio-knobs.conf"
settings = {
    "SOLOIST": "ws://127.0.0.1:3679",
    "GPIOCHIP": "/dev/gpiochip0",
    "LEFT_A": "14", "LEFT_B": "15", "LEFT_BUTTON": "4",
    "RIGHT_A": "23", "RIGHT_B": "24", "RIGHT_BUTTON": "17",
    "LED": "26",
    "LEFT_INVERT": "no", "RIGHT_INVERT": "no",
    "TRANSITIONS_PER_DETENT": "4",  # 4 for full-step encoders such as the PEC11 here, 2 for half-step
    "VOLUME_STEP": "4",             # Spotify volume steps per detent (out of 100)
    "HOLD_TIME": "3",               # seconds for a long press
    "SHUFFLE": "yes",               # start favourites in shuffle mode
    "FAVOURITES": "/var/lib/radio-knobs/favourites",
    "STATE": "/var/lib/radio-knobs/current",
    "POWEROFF": "sudo -n systemctl poweroff",
}

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("radio-knobs")


def load_config():
    try:
        with open(CONFIG) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    settings[key.strip()] = value.strip().strip('"')
    except OSError:
        pass


def yes(key):
    return settings[key].lower() in ("yes", "true", "1")


# --- Soloist API ------------------------------------------------------------

# Events that change one field of the playback_state snapshot
UPDATES = {
    "playback_changed": "status",
    "volume_changed": "volume",
    "context_changed": "context",
    "track_changed": "item",
    "device_changed": "is_active",
}


class Soloist:
    """Keeps a WebSocket to Soloist open (reconnecting when Soloist restarts)
    and the latest playback state at hand; on_change runs after every update."""

    def __init__(self, url, on_change):
        self.on_change = on_change
        self.lock = threading.Lock()
        self.state = {}
        self.ws = websocket.WebSocketApp(url, on_message=self.received, on_close=self.closed)
        threading.Thread(target=self.run, daemon=True).start()

    def run(self):
        while True:
            self.ws.run_forever()  # returns when the connection fails or closes
            time.sleep(3)

    def received(self, ws, message):
        try:
            event = json.loads(message)
        except ValueError:
            return
        kind = event.get("type")
        with self.lock:
            if kind == "playback_state":
                self.state = event
            elif kind in UPDATES:
                self.state[UPDATES[kind]] = event.get(UPDATES[kind])
            elif kind == "auth_state" and not event.get("logged_in"):
                self.state = {}
        if kind == "auth_state" and event.get("logged_in"):
            self.command("get_state")  # also after someone logs in later
        elif kind == "error":
            log.warning("soloist: %s", event.get("message"))
        self.on_change()

    def closed(self, ws, code, reason):
        with self.lock:
            self.state = {}
        self.on_change()

    def command(self, name, **fields):
        try:
            self.ws.send(json.dumps(dict(type="command", command=name, **fields)))
            return True
        except (websocket.WebSocketException, OSError, AttributeError) as e:
            log.warning("%s failed: %s", name, e)
            return False

    def get(self, key, default=None):
        with self.lock:
            return self.state.get(key, default)

    def active(self):
        """Whether this radio is the device Spotify is playing on (or paused at)."""
        return bool(self.get("is_active")) and self.get("status", "idle") != "idle"

    def playing(self):
        return self.active() and self.get("status") in ("playing", "buffering") and self.get("volume", 0) > 0

    def change_volume(self, delta):
        with self.lock:
            volume = max(0, min(100, self.state.get("volume", 0) + delta))
            self.state["volume"] = volume  # quick turns add up before Soloist reports back
        self.command("set_volume", volume=volume)
        self.on_change()

    def play(self, uri, shuffle=False):
        if not self.get("is_active"):
            self.command("activate")
        if not shuffle:
            return self.command("play", uri=uri)
        # Shuffle before playing, so the playlist starts at a random track, and
        # again after, in case Soloist ignored the first one while idle
        self.command("set_shuffle", enabled=True)
        ok = self.command("play", uri=uri)
        self.command("set_shuffle", enabled=True)
        return ok


player = None


# --- favourites -------------------------------------------------------------

# One playlist (or album) per line, as spotify:playlist:ID or an open.spotify.com
# link; anything after "#" is a comment, e.g. the playlist's name
def to_uri(entry):
    entry = entry.split("#", 1)[0].strip()
    if entry.startswith("https://open.spotify.com/"):
        kind, ident = entry.split("?", 1)[0].rstrip("/").split("/")[-2:]
        entry = "spotify:%s:%s" % (kind, ident)
    return entry


def favourites():
    try:
        with open(settings["FAVOURITES"]) as f:
            return [uri for uri in (to_uri(line) for line in f) if uri]
    except OSError:
        return []


def current_index():
    try:
        with open(settings["STATE"]) as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return 0


def store_index(index):
    try:
        with open(settings["STATE"], "w") as f:
            f.write(str(index))
    except OSError as e:
        log.warning("cannot store favourite: %s", e)


def play_favourite(index):
    uris = favourites()
    if not uris:
        log.info("no favourites yet (hold the right knob to add the playing playlist)")
        return False
    index %= len(uris)
    store_index(index)
    log.info("favourite %d: %s", index + 1, uris[index])
    return player.play(uris[index], shuffle=yes("SHUFFLE"))


def add_favourite():
    context = player.get("context") or {}
    uri = context.get("uri") or ""
    if not uri.startswith("spotify:") or uri in favourites():
        return False
    name = context.get("decorations", {}).get("identity", {}).get("name") or ""
    with open(settings["FAVOURITES"], "a") as f:
        f.write(uri + ("  # " + name if name else "") + "\n")
    log.info("added favourite %s %s", uri, name)
    return True


# --- LED --------------------------------------------------------------------

class StatusLed:
    """The LED on its own output line. Blinking runs in a short-lived thread;
    a newer blink (or show) takes over from an older one."""

    def __init__(self, chip, pin):
        self.pin = pin
        self.request = gpiod.request_lines(chip, consumer="radio-knobs", config={
            pin: gpiod.LineSettings(direction=Direction.OUTPUT, output_value=Value.INACTIVE)})
        self.lock = threading.Lock()  # a line request is not thread-safe
        self.playing = False
        self.blinking = 0  # id of the running blink, 0 = none

    def set(self, on):
        self.request.set_value(self.pin, Value.ACTIVE if on else Value.INACTIVE)

    def show(self, playing):
        with self.lock:
            self.playing = playing
            if not self.blinking:
                self.set(playing)

    def blink(self, times, on=0.15, off=0.15):
        """Blink a number of times, or forever with times None."""
        with self.lock:
            self.blinking += 1
            me = self.blinking
        threading.Thread(target=self.run_blink, args=(me, times, on, off), daemon=True).start()

    def run_blink(self, me, times, on, off):
        count = 0
        while times is None or count < times:
            for value, duration in ((True, on), (False, off)):
                with self.lock:
                    if self.blinking != me:
                        return
                    self.set(value)
                time.sleep(duration)
            count += 1
        with self.lock:
            if self.blinking == me:
                self.blinking = 0
                self.set(self.playing)


# --- rotary encoders and push buttons --------------------------------------

# Quadrature decoding with a transition table: invalid (bouncing) transitions
# count as 0, so no debounce delay is needed. A step is only taken when the knob
# reaches a click position, where both contacts are open (and, on half-step
# encoders, also where both are closed); bounce that falls back into the same
# click adds up to zero there.
TRANSITIONS = {
    (0b00, 0b01): +1, (0b01, 0b11): +1, (0b11, 0b10): +1, (0b10, 0b00): +1,
    (0b00, 0b10): -1, (0b10, 0b11): -1, (0b11, 0b01): -1, (0b01, 0b00): -1,
}


class Encoder:
    def __init__(self, pin_a, pin_b, per_detent, invert, on_step):
        self.pin_a, self.pin_b = pin_a, pin_b
        self.pins = (pin_a, pin_b)
        self.rest = (0b00,) if per_detent == 4 else (0b00, 0b11)
        self.threshold = per_detent // 2  # tolerates a missed edge on a fast turn
        self.sign = -1 if invert else 1
        self.on_step = on_step
        self.levels = {pin_a: 0, pin_b: 0}
        self.state = 0
        self.count = 0

    def start(self, levels):
        self.levels.update((pin, levels[pin]) for pin in self.pins)
        self.state = self.read()

    def read(self):
        return (self.levels[self.pin_a] << 1) | self.levels[self.pin_b]

    def edge(self, pin, active):
        self.levels[pin] = int(active)
        new = self.read()
        self.count += TRANSITIONS.get((self.state, new), 0)
        self.state = new
        if new in self.rest:
            count, self.count = self.count, 0
            if abs(count) >= self.threshold:
                self.on_step(self.sign * (1 if count > 0 else -1))


class PushButton:
    def __init__(self, pin, hold_time, on_press, on_hold):
        self.pins = (pin,)
        self.hold_time = hold_time
        self.on_press = on_press
        self.on_hold = on_hold
        self.pressed_at = None
        self.held = False

    def start(self, levels):
        pass  # a button already pressed at start counts from its next press

    def edge(self, pin, active):
        if active:
            self.pressed_at = time.monotonic()
            self.held = False
        elif self.pressed_at is not None:
            self.pressed_at = None
            if not self.held:
                self.on_press()

    def deadline(self):
        """When the press becomes a hold, or None."""
        if self.pressed_at is None or self.held:
            return None
        return self.pressed_at + self.hold_time

    def check(self, now):
        deadline = self.deadline()
        if deadline is not None and now >= deadline:
            self.held = True
            self.on_hold()


def watch_knobs(chip, encoders, buttons):
    """Run the knobs forever. Pins are active low (pull-up, contact to ground);
    the kernel debounces the buttons. Sleeps until an edge or a hold is due."""
    by_pin = {pin: knob for knob in encoders + buttons for pin in knob.pins}
    encoder_pins = tuple(pin for knob in encoders for pin in knob.pins)
    button_pins = tuple(pin for knob in buttons for pin in knob.pins)
    common = dict(direction=Direction.INPUT, edge_detection=Edge.BOTH, bias=Bias.PULL_UP, active_low=True)
    with gpiod.request_lines(chip, consumer="radio-knobs", config={
            encoder_pins: gpiod.LineSettings(**common),
            button_pins: gpiod.LineSettings(debounce_period=timedelta(milliseconds=50), **common),
    }) as request:
        levels = {pin: int(value == Value.ACTIVE)
                  for pin, value in zip(request.offsets, request.get_values())}
        for knob in encoders + buttons:
            knob.start(levels)
        while True:
            deadlines = [d for d in (button.deadline() for button in buttons) if d is not None]
            timeout = max(0.0, min(deadlines) - time.monotonic()) if deadlines else None
            if request.wait_edge_events(timeout):
                for event in request.read_edge_events():
                    by_pin[event.line_offset].edge(
                        event.line_offset, event.event_type == gpiod.EdgeEvent.Type.RISING_EDGE)
            now = time.monotonic()
            for button in buttons:
                button.check(now)


# --- knob actions -----------------------------------------------------------

def main():
    global player
    load_config()
    chip = settings["GPIOCHIP"]
    led = StatusLed(chip, int(settings["LED"]))
    player = Soloist(settings["SOLOIST"], lambda: led.show(player is not None and player.playing()))
    per_detent = int(settings["TRANSITIONS_PER_DETENT"])
    step = int(settings["VOLUME_STEP"])
    hold_time = float(settings["HOLD_TIME"])

    def volume(direction):
        player.change_volume(direction * step)

    def skip(direction):
        log.info("skip %s", "next" if direction > 0 else "previous")
        player.command("skip_next" if direction > 0 else "skip_prev")

    def play_pause():
        if not player.active():
            # Not playing here: start the last used favourite
            play_favourite(current_index())
        elif player.get("status") == "paused":
            player.command("play")
        else:
            player.command("pause")

    def next_favourite():
        if play_favourite(current_index() + 1):
            led.blink(2)

    def hold_favourite():
        if add_favourite():
            led.blink(3)

    def shutdown():
        log.info("shutting down")
        led.blink(None, 0.08, 0.08)
        player.command("pause")
        subprocess.run(settings["POWEROFF"].split())

    encoders = [
        Encoder(int(settings["LEFT_A"]), int(settings["LEFT_B"]), per_detent, yes("LEFT_INVERT"), volume),
        Encoder(int(settings["RIGHT_A"]), int(settings["RIGHT_B"]), per_detent, yes("RIGHT_INVERT"), skip),
    ]
    buttons = [
        PushButton(int(settings["LEFT_BUTTON"]), hold_time, play_pause, shutdown),
        PushButton(int(settings["RIGHT_BUTTON"]), hold_time, next_favourite, hold_favourite),
    ]
    log.info("radio-knobs ready (%d favourites)", len(favourites()))
    watch_knobs(chip, encoders, buttons)  # runs forever


if __name__ == "__main__":
    main()
