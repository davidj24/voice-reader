# Voice reader

Reads text aloud on this PC with the Kokoro voice model, with pause, rewind, skip and a progress bar.
It runs separately from Claude and uses no tokens.

| Keys (hold Left Alt) | What happens |
|---|---|
| **T** | Read the highlighted text, in any app |
| **Y** | Pause / resume |
| **K** / **L** | Back / forward 10 seconds |
| **O** / **P** | Previous / next sentence |
| **X** | Stop |

For Claude, the **Voices** pane (the `voice-picker` mod in claude-mods) has the auto-read button, the voice
list, the speed slider and the player. With auto-read on, each finished reply is read with no key press.

## How it fits together

- `reader.py`: background service. One thread generates sentences up to 45 seconds ahead and keeps them in memory;
  another feeds them to `aplay` in 40 ms blocks. That's why pausing is instant and rewinding needs no new work.
- `textclean.py`: rewrites lists, tables, units, money, abbreviations, paths and so on into speakable text and
  splits it into sentences. It only looks at formatting, so any writing style works.
- `selection_helper.py`: reads the highlighted text through XWayland.
- `voice-reader.service`: systemd user service (symlinked into `~/.config/systemd/user/`).
- `~/Kanata/kanata.kbd`: the hotkeys send `reader-*` messages over kanata's TCP port.

The Kokoro model and Python environment are shared with the read-aloud plugin, in
`~/.local/share/claude-read-aloud/`. On a new machine, run `/read-aloud:voice-setup` once to create them,
or point `VOICE_READER_DATA` at a folder with the same layout.

Settings are in `~/.config/voice-reader/config.json`.

## Fixing a word it says wrong

Add it to `~/.config/voice-reader/pronunciations.json`. It's read fresh for each reading:

    {"Kanata": "ka nah ta", "Qwen": "chwen"}

Rules for numbers, units and abbreviations are in the tables at the top of `textclean.py`.

## Control it from anything

    S=$XDG_RUNTIME_DIR/voice-reader.sock
    curl --unix-socket $S localhost/status
    echo "Hello there." | curl --unix-socket $S --data-binary @- 'localhost/speak?source=api'
    curl --unix-socket $S -X POST localhost/toggle                 # also pause, resume, stop
    curl --unix-socket $S -X POST 'localhost/skip?by=-10'          # also seek?to=30, sentence?by=1

## Useful commands

    journalctl --user -u voice-reader -f
    systemctl --user restart voice-reader
    python3 -m unittest discover tests                             # text cleanup tests

The model uses about 520 MB of memory while loaded and unloads after 10 idle minutes.
