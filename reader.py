#!/usr/bin/env python3
"""Background service that reads text aloud with Kokoro, with pause, rewind and seek.

Text arrives over a Unix socket (from the Claude mod, or anything else) or from a kanata hotkey that
reads the highlighted text. The voice model and the audio output are kept apart: one thread generates
sentences a little ahead of the listener and keeps them in memory, another feeds them to the sound
card a few milliseconds at a time. That is what makes pausing instant and rewinding free.

Control it with curl:
    curl --unix-socket $XDG_RUNTIME_DIR/voice-reader.sock localhost/status
    curl --unix-socket $XDG_RUNTIME_DIR/voice-reader.sock -X POST localhost/toggle
"""

import gc
import json
import os
import pathlib
import re
import socket
import socketserver
import subprocess
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

import numpy as np

from textclean import to_sentences

HERE = pathlib.Path(__file__).resolve().parent
HOME = pathlib.Path.home()
RUNTIME = pathlib.Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
SOCKET_PATH = RUNTIME / "voice-reader.sock"
CONFIG_PATH = HOME / ".config" / "voice-reader" / "config.json"
OLD_CONFIG_PATH = HOME / ".config" / "claude-read-aloud" / "config.json"
DATA = pathlib.Path(os.environ.get("VOICE_READER_DATA", HOME / ".local/share/claude-read-aloud"))
MODEL = DATA / "kokoro" / "kokoro-v1.0.onnx"
VOICES = DATA / "kokoro" / "voices-v1.0.bin"
KANATA_ADDR = ("127.0.0.1", 7070)

RATE = 24000
BLOCK = RATE // 25  # 40 ms: how much audio is handed over at a time
LEAD = 0.20  # seconds of audio allowed in the sound system ahead of the listener
LOOKAHEAD = 45.0  # seconds generated ahead, so stopping early wastes little CPU
UNLOAD_AFTER = 600.0  # free the model's memory after this long with nothing to generate
SECONDS_PER_CHAR = 0.062  # first guess at the length of unread text, at speed 1

LANGS = {"a": "en-us", "b": "en-gb", "e": "es", "f": "fr-fr", "h": "hi", "i": "it", "j": "ja",
         "p": "pt-br", "z": "cmn"}
DEFAULTS = {"voice": "af_heart", "speed": 1.0, "auto_read": True}
PLAYER = ["aplay", "-q", "-t", "raw", "-f", "S16_LE", "-r", str(RATE), "-c", "1", "--buffer-time=120000"]


def log(*parts):
    print(*parts, flush=True)


def load_config() -> dict:
    cfg = dict(DEFAULTS)
    source = CONFIG_PATH if CONFIG_PATH.exists() else OLD_CONFIG_PATH  # carries over your old settings
    try:
        saved = json.loads(source.read_text())
        cfg.update({k: saved[k] for k in DEFAULTS if k in saved})
    except (OSError, ValueError):
        pass
    return cfg


def save_config(cfg: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2) + "\n")
    tmp.replace(CONFIG_PATH)


class Segment:
    __slots__ = ("text", "pause", "audio")

    def __init__(self, text: str, pause: float):
        self.text = text
        self.pause = pause
        self.audio = None  # int16 samples including the pause; None until generated


class Reading:
    def __init__(self, sentences, source: str, voice: str, speed: float):
        self.segments = [Segment(s.text, s.pause) for s in sentences]
        self.source = source
        self.voice = voice
        self.speed = speed
        self.index = 0  # the sentence being heard
        self.offset = 0  # samples into that sentence


class Engine:
    def __init__(self):
        self.cv = threading.Condition()
        self.cfg = load_config()
        self.reading: Reading | None = None
        self.state = "idle"  # idle | playing | paused | finished
        self.generation = 0  # bumped by anything that must interrupt the audio being played
        self.buffering = False
        self.seconds_per_char = SECONDS_PER_CHAR  # at speed 1; learned as sentences are generated
        self.kokoro = None
        self.last_synth = 0.0
        threading.Thread(target=self._synth_loop, daemon=True, name="synth").start()
        threading.Thread(target=self._play_loop, daemon=True, name="play").start()

    # ---------------------------------------------------------------- timeline helpers

    def _estimate(self, seg: Segment, r: Reading) -> float:
        return len(seg.text) * self.seconds_per_char / r.speed + seg.pause

    def _duration(self, seg: Segment, r: Reading) -> float:
        return len(seg.audio) / RATE if seg.audio is not None else self._estimate(seg, r)

    def _position(self, r: Reading) -> float:
        before = sum(self._duration(s, r) for s in r.segments[: r.index])
        return before + r.offset / RATE

    def _interrupt(self):
        self.generation += 1
        self.cv.notify_all()

    # ---------------------------------------------------------------- commands

    def speak(self, text: str, source: str = "api", voice: str | None = None) -> bool:
        sentences = to_sentences(text)
        with self.cv:
            if not sentences:
                return False
            self.reading = Reading(sentences, source, voice or self.cfg["voice"], float(self.cfg["speed"]))
            self.state = "playing"
            self.buffering = True
            self._interrupt()
        log(f"speak: {len(sentences)} sentences from {source}")
        return True

    def toggle(self):
        with self.cv:
            if self.state == "playing":
                self.state = "paused"
            elif self.state == "paused":
                self.state = "playing"
            elif self.state == "finished" and self.reading:
                self.reading.index, self.reading.offset = 0, 0
                self.state = "playing"
            self._interrupt()

    def pause(self):
        with self.cv:
            if self.state == "playing":
                self.state = "paused"
                self._interrupt()

    def resume(self):
        with self.cv:
            if self.state == "paused":
                self.state = "playing"
                self._interrupt()

    def stop(self):
        with self.cv:
            self.reading = None
            self.state = "idle"
            self.buffering = False
            self._interrupt()

    def _seek_locked(self, seconds: float):
        r = self.reading
        if r is None:
            return
        remaining = max(0.0, seconds)
        for i, seg in enumerate(r.segments):
            length = self._duration(seg, r)
            if remaining < length or i == len(r.segments) - 1:
                r.index = i
                inside = min(remaining, length)
                r.offset = 0 if seg.audio is None else min(int(inside * RATE), len(seg.audio))
                break
            remaining -= length
        if self.state == "finished":
            self.state = "playing"
        self._interrupt()

    def seek(self, seconds: float):
        with self.cv:
            self._seek_locked(seconds)

    def skip(self, seconds: float):
        with self.cv:
            if self.reading:
                self._seek_locked(self._position(self.reading) + seconds)

    def sentence(self, step: int):
        with self.cv:
            r = self.reading
            if r is None:
                return
            if step < 0 and r.offset > RATE * 2:  # like a music player: back goes to the start first
                r.offset = 0
            else:
                r.index = min(len(r.segments) - 1, max(0, r.index + step))
                r.offset = 0
            if self.state == "finished":
                self.state = "playing"
            self._interrupt()

    def set_config(self, voice=None, speed=None, auto_read=None):
        with self.cv:
            if voice is not None:
                self.cfg["voice"] = voice
            if speed is not None:
                self.cfg["speed"] = round(min(2.0, max(0.5, float(speed))), 2)
            if auto_read is not None:
                self.cfg["auto_read"] = bool(auto_read)
            save_config(self.cfg)
            r = self.reading
            if r and (r.voice != self.cfg["voice"] or r.speed != self.cfg["speed"]):
                # The current reading picks up the change, from the start of this sentence.
                r.voice, r.speed = self.cfg["voice"], self.cfg["speed"]
                for seg in r.segments:
                    seg.audio = None
                r.offset = 0
                self._interrupt()

    def status(self) -> dict:
        with self.cv:
            r = self.reading
            out = {
                "state": self.state,
                "buffering": self.buffering and self.state == "playing",
                "voice": self.cfg["voice"],
                "speed": self.cfg["speed"],
                "autoRead": self.cfg["auto_read"],
                "modelLoaded": self.kokoro is not None,
                "position": 0.0,
                "duration": 0.0,
                "isEstimate": False,
                "index": 0,
                "count": 0,
                "text": "",
                "source": "",
            }
            if r:
                out.update(
                    position=round(self._position(r), 2),
                    duration=round(sum(self._duration(s, r) for s in r.segments), 2),
                    isEstimate=any(s.audio is None for s in r.segments),
                    index=r.index,
                    count=len(r.segments),
                    text=r.segments[r.index].text,
                    source=r.source,
                )
            return out

    # ---------------------------------------------------------------- generating speech

    def _next_job(self):
        """The first sentence not yet generated, from the listener's position, within the lookahead."""
        r = self.reading
        if r is None or self.state in ("idle", "finished"):
            return None
        ahead = -r.offset / RATE
        for i in range(r.index, len(r.segments)):
            seg = r.segments[i]
            if seg.audio is None:
                return r, seg, r.voice, r.speed
            ahead += len(seg.audio) / RATE
            if ahead > LOOKAHEAD:
                return None
        return None

    def _synth_loop(self):
        while True:
            with self.cv:
                job = self._next_job()
                while job is None:
                    idle = time.monotonic() - self.last_synth
                    if self.kokoro is not None and idle > UNLOAD_AFTER:
                        self.kokoro = None
                        gc.collect()
                        log("model unloaded (idle)")
                        self.cv.notify_all()
                    self.cv.wait(timeout=30)
                    job = self._next_job()
            r, seg, voice, speed = job
            try:
                audio = self._synthesize(seg, voice, speed)
            except Exception:
                traceback.print_exc()
                audio = np.zeros(0, dtype=np.int16)
            with self.cv:
                self.last_synth = time.monotonic()
                # Thrown away if the text, voice or speed changed while it was being made.
                if r is self.reading and r.voice == voice and r.speed == speed and seg.audio is None:
                    seg.audio = audio
                    if len(audio) > RATE:
                        spoken = (len(audio) / RATE - seg.pause) * speed / max(1, len(seg.text))
                        self.seconds_per_char = 0.8 * self.seconds_per_char + 0.2 * spoken
                self.cv.notify_all()

    def _synthesize(self, seg: Segment, voice: str, speed: float) -> np.ndarray:
        if self.kokoro is None:
            from kokoro_onnx import Kokoro

            started = time.monotonic()
            self.kokoro = Kokoro(str(MODEL), str(VOICES))
            log(f"model loaded in {time.monotonic() - started:.1f}s")
        samples, _ = self.kokoro.create(seg.text, voice=voice, speed=speed, lang=LANGS.get(voice[:1], "en-us"))
        pcm = (np.clip(samples, -1, 1) * 32767).astype(np.int16)
        return np.concatenate([pcm, np.zeros(int(seg.pause * RATE), dtype=np.int16)])

    # ---------------------------------------------------------------- playing it

    def _ready(self) -> bool:
        r = self.reading
        if self.state != "playing" or r is None:
            return False
        # A sentence heard to its end hands over to the next, which may not be generated yet.
        while r.index + 1 < len(r.segments):
            audio = r.segments[r.index].audio
            if audio is None or r.offset < len(audio):
                break
            r.index += 1
            r.offset = 0
        return r.segments[r.index].audio is not None

    def _play_loop(self):
        while True:
            with self.cv:
                while not self._ready():
                    self.buffering = self.state == "playing"
                    self.cv.wait()
                self.buffering = False
                generation = self.generation
                r = self.reading
            try:
                self._stream(r, generation)
            except Exception:
                traceback.print_exc()
                with self.cv:
                    self.state = "idle"
                time.sleep(0.5)

    def _stream(self, r: Reading, generation: int):
        """Plays from the listener's position until interrupted, out of audio, or at the end."""
        player = subprocess.Popen(PLAYER, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
        written = 0  # samples handed to the player in this stream
        heard = 0  # of those, how many the listener has reached
        started = time.monotonic()
        cursor_i, cursor_o = r.index, r.offset  # where the next block comes from
        reached_end = False

        def advance_heard():
            nonlocal heard
            target = min(written, int((time.monotonic() - started) * RATE))
            while heard < target:
                seg_audio = r.segments[r.index].audio
                length = 0 if seg_audio is None else len(seg_audio)
                step = min(target - heard, length - r.offset)
                if step > 0:
                    r.offset += step
                    heard += step
                elif r.index + 1 < len(r.segments):
                    r.index += 1
                    r.offset = 0
                else:
                    heard = target

        try:
            while True:
                with self.cv:
                    if self.generation != generation or self.state != "playing":
                        return
                    advance_heard()
                    while cursor_i < len(r.segments):  # step over finished or empty sentences
                        audio = r.segments[cursor_i].audio
                        if audio is None or cursor_o < len(audio):
                            break
                        cursor_i += 1
                        cursor_o = 0
                    if cursor_i >= len(r.segments):
                        reached_end = True
                        audio = None
                    else:
                        audio = r.segments[cursor_i].audio
                    if audio is None and not reached_end:  # generation has fallen behind
                        if heard >= written:
                            return  # stop the stream; the main loop waits for more audio
                        block = None
                    elif reached_end:
                        if heard >= written:
                            self.state = "finished"
                            r.index, r.offset = len(r.segments) - 1, 0 if r.segments[-1].audio is None else len(r.segments[-1].audio)
                            self.cv.notify_all()
                            return
                        block = None
                    else:
                        block = audio[cursor_o : cursor_o + BLOCK]
                        cursor_o += len(block)
                if block is None:
                    time.sleep(0.02)
                    continue
                player.stdin.write(block.tobytes())
                player.stdin.flush()
                written += len(block)
                # Stay only slightly ahead of the listener, so stopping is immediate.
                while written / RATE - (time.monotonic() - started) > LEAD:
                    with self.cv:
                        if self.generation != generation or self.state != "playing":
                            return
                        advance_heard()
                    time.sleep(0.01)
        except BrokenPipeError:
            log("player exited early")
            time.sleep(0.2)
        finally:
            player.kill()
            player.wait()
            with self.cv:
                advance_heard()
                self.cv.notify_all()


# ----------------------------------------------------------------------- control socket

class Handler(BaseHTTPRequestHandler):
    engine: Engine

    def log_message(self, *args):
        pass

    def address_string(self):
        return "local"

    def _reply(self, body: dict, code: int = 200):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if urlparse(self.path).path == "/status":
            self._reply(self.engine.status())
        else:
            self._reply({"error": "unknown path"}, 404)

    def do_POST(self):
        url = urlparse(self.path)
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode("utf-8", "replace") if length else ""
        engine = self.engine
        action = url.path.strip("/")
        try:
            if action == "speak":
                if query.get("source") == "claude" and not engine.cfg["auto_read"]:
                    return self._reply({"ok": False, "reason": "auto-read is off"})
                return self._reply({"ok": engine.speak(body, query.get("source", "api"), query.get("voice"))})
            if action == "toggle":
                engine.toggle()
            elif action == "pause":
                engine.pause()
            elif action == "resume":
                engine.resume()
            elif action == "stop":
                engine.stop()
            elif action == "seek":
                engine.seek(float(query["to"]))
            elif action == "skip":
                engine.skip(float(query["by"]))
            elif action == "sentence":
                engine.sentence(int(query["by"]))
            elif action == "config":
                auto = query.get("autoRead")
                engine.set_config(query.get("voice"), query.get("speed"),
                                  None if auto is None else auto == "true")
            elif action == "selection":
                threading.Thread(target=read_selection, args=(engine,), daemon=True).start()
            else:
                return self._reply({"error": "unknown path"}, 404)
        except (KeyError, ValueError) as e:
            return self._reply({"error": f"bad request: {e}"}, 400)
        self._reply({"ok": True})


class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    request_queue_size = 32


# ------------------------------------------------------------------------ hotkeys

def notify(text: str):
    subprocess.run(["notify-send", "-a", "Voice reader", "-t", "2500", "Voice reader", text], check=False,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def read_selection(engine: Engine):
    """Reads whatever is highlighted, in any app, through the X11 side of the primary selection."""
    try:
        result = subprocess.run(["/usr/bin/python3", "-I", str(HERE / "selection_helper.py")],
                                capture_output=True, text=True, timeout=5)
        text = result.stdout.strip()
    except subprocess.TimeoutExpired:
        text = ""
    if not text:
        notify("Nothing is highlighted.")
        return
    if not engine.speak(text, "selection"):
        notify("That selection has nothing to read.")


def kanata_loop(engine: Engine):
    actions = {
        "reader-read": lambda: read_selection(engine),
        "reader-toggle": engine.toggle,
        "reader-stop": engine.stop,
        "reader-back": lambda: engine.skip(-10),
        "reader-forward": lambda: engine.skip(10),
        "reader-prev": lambda: engine.sentence(-1),
        "reader-next": lambda: engine.sentence(1),
    }
    while True:
        try:
            with socket.create_connection(KANATA_ADDR) as sock:
                log("connected to kanata")
                for line in sock.makefile("r", encoding="utf-8"):
                    try:
                        message = json.loads(line).get("MessagePush", {}).get("message")
                    except ValueError:
                        continue
                    if isinstance(message, list) and message:
                        message = message[0]
                    action = actions.get(message)
                    if action:
                        threading.Thread(target=action, daemon=True).start()
        except OSError:
            pass
        time.sleep(3)


def main():
    if not MODEL.exists():
        sys.exit(f"Kokoro model not found at {MODEL}. Run setup.sh.")
    engine = Engine()
    Handler.engine = engine
    SOCKET_PATH.unlink(missing_ok=True)
    server = Server(str(SOCKET_PATH), Handler)
    os.chmod(SOCKET_PATH, 0o600)
    threading.Thread(target=kanata_loop, args=(engine,), daemon=True, name="kanata").start()
    log(f"listening on {SOCKET_PATH}")
    server.serve_forever()


if __name__ == "__main__":
    main()
