"""Exercises pause, rewind, seek and speed against the real model, with the sound output replaced.

Needs Kokoro, so run it with the environment that has it:
    ~/.local/share/claude-read-aloud/venv/bin/python tests/check_engine.py
"""

import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import reader

reader.PLAYER = ["sh", "-c", "cat > /dev/null"]
reader.save_config = lambda cfg: None  # never touch the real settings

TEXT = """## First part

The reader has to generate this sentence ahead of time. It keeps each sentence in memory after that.

- It can pause at any point in the text.
- It can go back ten seconds without any new work.

The last paragraph is here so the whole passage takes long enough to skip around in. It should still end cleanly."""


def wait(engine, test, seconds=30, what=""):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        s = engine.status()
        if test(s):
            return s
        time.sleep(0.02)
    raise SystemExit(f"FAILED waiting for: {what}; status={engine.status()}")


def main():
    e = reader.Engine()
    assert e.speak(TEXT, "test")
    s = wait(e, lambda s: s["position"] > 1.0, what="playback to start")
    print("playing:", s["position"], "of", s["duration"], "estimate" if s["isEstimate"] else "exact", repr(s["text"][:40]))

    e.pause()
    time.sleep(0.15)  # the player thread settles the exact stopping point
    a = e.status()["position"]
    time.sleep(0.6)
    b = e.status()["position"]
    assert a == b, f"moved while paused: {a} -> {b}"
    print("pause holds at", b)

    e.resume()
    s = wait(e, lambda s: s["position"] > b + 0.5, what="resume")
    print("resumed:", s["position"])

    before = e.status()["position"]
    e.skip(-1.0)
    after = e.status()["position"]
    assert after < before, f"rewind did not move back: {before} -> {after}"
    print("rewound:", round(before, 2), "->", round(after, 2))

    e.skip(4.0)
    s = wait(e, lambda s: s["position"] > after + 3.0, what="skip forward")
    print("skipped forward:", s["position"], "sentence", s["index"])

    i = e.status()["index"]
    e.sentence(1)
    s = wait(e, lambda s: s["index"] == i + 1, what="next sentence")
    print("next sentence:", s["index"], repr(s["text"][:40]))

    e.seek(0.5)
    s = e.status()
    assert s["index"] == 0 and s["position"] < 2, s
    print("seek to start:", s["position"])

    e.set_config(speed=1.5)
    s = wait(e, lambda s: s["position"] > 0.3 and s["speed"] == 1.5, what="speed change")
    print("speed changed, still reading:", s["position"])

    e.seek(e.status()["duration"] - 1.0)
    s = wait(e, lambda s: s["state"] == "finished", seconds=40, what="the end")
    print("finished at", s["position"], "of", s["duration"], "estimate" if s["isEstimate"] else "exact")

    e.toggle()
    s = wait(e, lambda s: s["state"] == "playing" and s["position"] > 0.2, what="replay")
    print("replay from start:", s["position"])

    e.stop()
    s = wait(e, lambda s: s["state"] == "idle", what="stop")
    print("stopped")
    print("ALL OK")


main()
