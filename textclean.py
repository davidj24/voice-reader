"""Turns text meant for the eye into text meant for the ear.

The voice model reads what it is given, so "5 km/h" comes out as "five K M slash H". This module
rewrites formatting, numbers with units, abbreviations, paths and so on before Kokoro sees them, and
splits the result into sentences so the reader can pause, rewind and skip by sentence.

It only looks at the shape of the text (markdown, digits, symbols), never at writing style, so plain
English, ELI5 or ASD-STE100 replies all go through it the same way.

Add your own words in ~/.config/voice-reader/pronunciations.json: {"Kanata": "ka nah ta"}.
"""

import json
import pathlib
import re
from dataclasses import dataclass

MAX_SENTENCE = 320  # longer sentences are split at commas
MIN_SENTENCE = 18  # shorter ones join the next, because Kokoro sounds odd on fragments
MAX_TABLE_ROWS = 8

PAUSE_SENTENCE = 0.08
PAUSE_ITEM = 0.28
PAUSE_PARAGRAPH = 0.32
PAUSE_HEADING = 0.45

USER_WORDS = pathlib.Path.home() / ".config" / "voice-reader" / "pronunciations.json"


@dataclass
class Sentence:
    text: str
    pause: float = PAUSE_SENTENCE


# ------------------------------------------------------------------ inline rewriting

UNITS = {
    "TB": "terabyte", "GB": "gigabyte", "MB": "megabyte", "KB": "kilobyte",
    "TiB": "terabyte", "GiB": "gigabyte", "MiB": "megabyte", "KiB": "kilobyte",
    "ms": "millisecond", "µs": "microsecond", "ns": "nanosecond",
    "s": "second", "sec": "second", "secs": "second",
    "m": "minute", "min": "minute", "mins": "minute",
    "h": "hour", "hr": "hour", "hrs": "hour", "d": "day",
    "km": "kilometer", "cm": "centimeter", "mm": "millimeter", "mi": "mile", "ft": "foot",
    "kg": "kilogram", "mg": "milligram", "lb": "pound", "lbs": "pound", "oz": "ounce",
    "Hz": "hertz", "kHz": "kilohertz", "MHz": "megahertz", "GHz": "gigahertz",
    "W": "watt", "kW": "kilowatt", "kWh": "kilowatt hour", "px": "pixel",
    "fps": "frames per second", "rpm": "R P M", "mph": "miles per hour", "dpi": "D P I",
    "km/h": "kilometers per hour", "m/s": "meters per second", "ft/s": "feet per second",
    "GB/s": "gigabytes per second", "MB/s": "megabytes per second",
    "Mbps": "megabits per second", "Gbps": "gigabits per second",
    "°C": "degrees Celsius", "°F": "degrees Fahrenheit",
}
# Spelled the same in the singular, or already complete phrases.
NO_PLURAL = {"hertz", "kilohertz", "megahertz", "gigahertz", "R P M", "D P I"}
UNIT_RE = "|".join(re.escape(u) for u in sorted(UNITS, key=len, reverse=True))

ABBREVIATIONS = [
    (r"\be\.g\.?(?=[\s,:]|$)", "for example"),
    (r"\bi\.e\.?(?=[\s,:]|$)", "that is"),
    (r"\betc\.", "et cetera."),
    (r"\betc\b", "et cetera"),
    (r"\bvs\.?(?=\s)", "versus"),
    (r"\bapprox\.?(?=\s)", "approximately"),
    (r"\bw/o\b", "without"),
    (r"(?<![\w/])w/(?=\s|\w)", "with "),
    (r"\bb/c\b", "because"),
    (r"\bDr\.(?=\s)", "Doctor"),
    (r"\bMr\.(?=\s)", "Mister"),
    (r"\bMrs\.(?=\s)", "Missus"),
    (r"\bMs\.(?=\s)", "Miz"),
    (r"\bProf\.(?=\s)", "Professor"),
    (r"\bFig\.(?=\s)", "Figure"),
    (r"\bTL;DR\b", "T L D R"),
    (r"\bLaTeX\b", "lay tech"),
    (r"\bYAML\b", "yamel"),
    (r"\bGUI\b", "gooey"),
    (r"\bavg\b", "average"),
    (r"\bmax\.(?=\s)", "maximum"),
    (r"\bmin\.(?=\s)", "minimum"),
]

EXTENSIONS = {
    "py": "P Y", "ts": "T S", "tsx": "T S X", "js": "J S", "jsx": "J S X", "md": "M D",
    "json": "jason", "yaml": "yamel", "yml": "yamel", "sh": "S H", "txt": "text", "html": "H T M L",
    "css": "C S S", "toml": "toml", "csv": "C S V", "pdf": "P D F", "png": "P N G", "jpg": "J peg",
    "log": "log", "ipynb": "notebook", "rs": "R S", "go": "go", "kbd": "K B D", "service": "service",
    "tex": "tech", "wav": "wave", "mp3": "M P 3", "zip": "zip", "c": "C", "cpp": "C P P", "h": "H",
}
EXT_RE = "|".join(sorted(EXTENSIONS, key=len, reverse=True))

MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
          "October", "November", "December"]

EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF☀-➿⬀-⯿️‍]+"
)


def _user_words() -> list[tuple[re.Pattern, str]]:
    try:
        table = json.loads(USER_WORDS.read_text())
    except (OSError, ValueError):
        return []
    if not isinstance(table, dict):
        return []
    return [(re.compile(rf"(?<!\w){re.escape(k)}(?!\w)"), str(v)) for k, v in table.items()]


def _filename(name: str) -> str:
    match = re.fullmatch(rf"(.+)\.({EXT_RE})", name)
    if not match:
        return name
    return f"{match[1]} dot {EXTENSIONS[match[2]]}"


def _path(match: re.Match) -> str:
    whole = match[0].rstrip("/")
    line = re.search(r":(\d+)$", whole)
    if line:
        whole = whole[: line.start()]
    spoken = _filename(whole.rsplit("/", 1)[-1])
    return f"{spoken}, line {line[1]}" if line else spoken


def _money(match: re.Match) -> str:
    amount, scale = match[1].replace(",", ""), match[2]
    if scale:
        word = {"k": "thousand", "K": "thousand", "m": "million", "M": "million", "b": "billion",
                "B": "billion"}[scale.lower() if scale in "mb" else scale]
        return f"{match[1]} {word} dollars"
    dollars, _, cents = amount.partition(".")
    cents = (cents + "0")[:2] if cents else ""
    dollar_word = "dollar" if dollars == "1" else "dollars"
    if not cents or cents == "00":
        return f"{match[1].split('.')[0]} {dollar_word}"
    cent_word = "cent" if int(cents) == 1 else "cents"
    if int(dollars or 0) == 0:
        return f"{int(cents)} {cent_word}"
    return f"{dollars} {dollar_word} and {int(cents)} {cent_word}"


def _unit(match: re.Match) -> str:
    number, unit = match[1], match[2]
    word = UNITS[unit]
    if word.startswith(("degrees", "frames", "kilometers", "meters", "feet", "miles", "gigabytes",
                        "megabytes", "megabits", "gigabits")) or word in NO_PLURAL:
        return f"{number} {word}"
    if number == "1":
        return f"1 {word}"
    if word == "foot":
        return f"{number} feet"
    return f"{number} {word}s"


def _date(match: re.Match) -> str:
    month = int(match[2])
    if not 1 <= month <= 12:
        return match[0]
    return f"{MONTHS[month - 1]} {int(match[3])}, {match[1]}"


def _clock(match: re.Match) -> str:
    hour, minute, meridiem = match[1], match[2], match[3]
    tail = f" {meridiem[0].upper()} M" if meridiem else ""
    if minute == "00":
        return f"{hour} o'clock{tail}" if not meridiem else f"{hour}{tail}"
    if minute.startswith("0"):
        return f"{hour} oh {minute[1]}{tail}"
    return f"{hour} {minute}{tail}"


def _identifier(match: re.Match) -> str:
    return match[0].replace("_", " ")


def clean_inline(text: str) -> str:
    """Rewrites one line or sentence of text so it reads well aloud."""
    s = text

    # Markdown that survives inside a line.
    s = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", s)
    s = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", s)
    s = re.sub(r"</?[a-zA-Z][^>\n]*>", " ", s)
    s = s.replace("`", "")
    s = re.sub(r"(\*\*|__|\*(?=\S)|(?<=\S)\*|~~)", "", s)
    s = re.sub(r"(?<!\w)_(\S[^_]*\S)_(?!\w)", r"\1", s)
    s = EMOJI.sub(" ", s)

    # Your own words go first, so they win over every rule below.
    for pattern, spoken in _user_words():
        s = pattern.sub(spoken, s)

    for pattern, spoken in ABBREVIATIONS:
        s = re.sub(pattern, spoken, s, flags=re.IGNORECASE if pattern.startswith(r"\be") else 0)

    # Links, then paths, then single file names.
    s = re.sub(r"https?://(?:www\.)?([\w.-]+)[^\s)]*",
               lambda m: "link to " + m[1].rstrip(".").replace(".", " dot "), s)
    s = re.sub(r"(?<![\w/.])(?:~|\.{1,2})?(?:/[\w.@+-]+){2,}/?(?::\d+)?", _path, s)
    s = re.sub(rf"(?<![\w/.])[\w.-]+(?:/[\w.-]+)+\.(?:{EXT_RE})(?::\d+)?(?![\w])", _path, s)
    s = re.sub(rf"(?<![\w/.])[\w-]+(?:/[\w.-]+){{2,}}/?(?![\w])", _path, s)
    s = re.sub(rf"(?<![\w/-])([\w-]+\.(?:{EXT_RE}))(?::(\d+))?(?![\w.])",
               lambda m: _filename(m[1]) + (f", line {m[2]}" if m[2] else ""), s)

    # Dates and times, before hyphens and colons are looked at.
    s = re.sub(r"\b(\d{4})-(\d{2})-(\d{2})\b", _date, s)
    s = re.sub(r"\b(\d{1,2}):(\d{2})\s?([ap]m)\b", _clock, s, flags=re.IGNORECASE)
    s = re.sub(r"(?<![\d:])(\d{1,2}):(\d{2})()(?![\d:])", _clock, s)

    # Versions, money, then numbers with units.
    s = re.sub(r"\bv?(\d+\.\d+\.\d+)\b", lambda m: "version " + m[1].replace(".", " point "), s)
    s = re.sub(r"\bv(\d+(?:\.\d+)?)\b", lambda m: "version " + m[1].replace(".", " point "), s)
    s = re.sub(r"\$(\d[\d,]*(?:\.\d+)?)\s(thousand|million|billion)\b", r"\1 \2 dollars", s)
    s = re.sub(r"\$(\d[\d,]*(?:\.\d+)?)(?:\s?([kKmMbB])\b)?", _money, s)
    s = re.sub(rf"(\d+(?:\.\d+)?)\s?({UNIT_RE})(?![\w/])", _unit, s)
    s = re.sub(r"\b(\d+(?:\.\d+)?)k\b", r"\1 thousand", s)
    s = re.sub(r"\b(\d+(?:\.\d+)?)M\b", r"\1 million", s)
    s = re.sub(r"\b(\d+(?:\.\d+)?)B\b", r"\1 billion", s)

    # Numeric symbols.
    s = re.sub(r"(\d)\s?[–—]\s?(\d)", r"\1 to \2", s)
    s = re.sub(r"(?<![\d-])(\d{1,4}(?:\.\d+)?)-(\d{1,4}(?:\.\d+)?)(?![\d-])", r"\1 to \2", s)
    s = re.sub(r"(\d)\s?[×x](?=\s|$|[.,;:)])", r"\1 times", s)
    s = re.sub(r"(\d)\s?×\s?(\d)", r"\1 times \2", s)
    s = re.sub(r"[~≈]\s?(?=\d|\$)", "about ", s)
    s = re.sub(r"≤\s?(?=\d)", "at most ", s)
    s = re.sub(r"≥\s?(?=\d)", "at least ", s)
    s = re.sub(r"(?<=\s)<\s?(?=\d)", "under ", s)
    s = re.sub(r"^<\s?(?=\d)", "under ", s)
    s = re.sub(r"(?<=\s)>\s?(?=\d)", "over ", s)
    s = re.sub(r"^>\s?(?=\d)", "over ", s)
    s = re.sub(r"(\d)\+(?=\s|$|[.,;:)])", r"\1 plus", s)
    s = re.sub(r"#(?=\d)", "number ", s)

    # Commands, flags and code-style names.
    s = re.sub(r"(?<![\w/.])/([a-z][\w:-]*)", r"slash \1", s)
    s = re.sub(r"(?<![\w-])--?([a-zA-Z][\w-]*)", r"\1", s)
    s = re.sub(r"\b[A-Za-z]+(?:_[A-Za-z0-9]+)+\b", _identifier, s)
    s = re.sub(r"\b([a-z]+)([A-Z][a-z]+)\b", r"\1 \2", s)
    s = re.sub(r"\b([a-z]+)/([a-z]+)\b", r"\1 or \2", s)
    s = re.sub(r"(?<![\w])@([A-Za-z][\w-]*)", r"at \1", s)

    # Symbols that are noise when spoken.
    s = re.sub(r"\s*(?:→|->|⇒|=>)\s*", " to ", s)
    s = s.replace("&", " and ")
    s = re.sub(r"\s+=\s+", " equals ", s)
    s = re.sub(r"\s*[—–]\s*", ", ", s)
    s = re.sub(r"\s-\s", ", ", s)
    s = s.replace("…", "...")
    s = re.sub(r"[|\\#*^<>{}\[\]_~]", " ", s)

    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"\s+([,.;:!?])", r"\1", s)
    s = re.sub(r"([,;:])(?:\s*[,;:])+", r"\1", s)
    return s


# -------------------------------------------------------------------- sentence splitting

def _split_long(sentence: str) -> list[str]:
    parts, current = [], ""
    for piece in re.split(r"(?<=[,;:])\s+", sentence):
        while len(piece) > MAX_SENTENCE:  # no commas at all: cut at a space
            cut = piece.rfind(" ", 0, MAX_SENTENCE)
            cut = cut if cut > 0 else MAX_SENTENCE
            if current:
                parts.append(current)
                current = ""
            parts.append(piece[:cut])
            piece = piece[cut:].strip()
        if current and len(current) + len(piece) + 1 > MAX_SENTENCE:
            parts.append(current)
            current = piece
        else:
            current = f"{current} {piece}".strip()
    if current:
        parts.append(current)
    return parts


def split_sentences(block: str, last_pause: float) -> list[Sentence]:
    """Splits a block of cleaned text. The last sentence gets the block's own pause."""
    raw = re.split(r"(?<=[.!?])[\"')]*\s+(?=[\"'(]*[A-Z0-9])", block)
    merged: list[str] = []
    for sentence in raw:
        sentence = sentence.strip()
        if not sentence:
            continue
        if merged and len(merged[-1]) < MIN_SENTENCE:
            merged[-1] = f"{merged[-1]} {sentence}"
        else:
            merged.append(sentence)
    out = [Sentence(part) for sentence in merged for part in _split_long(sentence)]
    if out:
        out[-1].pause = last_pause
    return out


# ----------------------------------------------------------------------- block handling

def _cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _table(rows: list[str]) -> list[Sentence]:
    header = _cells(rows[0])
    body = [_cells(r) for r in rows[2:]]
    out: list[Sentence] = []
    for row in body[:MAX_TABLE_ROWS]:
        first = clean_inline(row[0]) if row else ""
        details = [
            f"{clean_inline(header[i])}: {clean_inline(cell)}"
            for i, cell in enumerate(row[1:], start=1)
            if i < len(header) and clean_inline(cell)
        ]
        spoken = first.rstrip(".") + ". " + ". ".join(details)
        out.extend(split_sentences(spoken.strip().rstrip(".") + ".", PAUSE_ITEM))
    hidden = len(body) - MAX_TABLE_ROWS
    if hidden > 0:
        out.append(Sentence(f"And {hidden} more rows on screen.", PAUSE_PARAGRAPH))
    return out


IS_TABLE_RULE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
IS_ROW = re.compile(r"^\s*\|.*\|\s*$")
IS_BULLET = re.compile(r"^\s*[-*+•◦▪‣]\s+(.*)$")
IS_NUMBERED = re.compile(r"^\s*(\d{1,3})[.)]\s+(.*)$")
IS_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$")
IS_RULE = re.compile(r"^\s*([-*_])(\s*\1){2,}\s*$")
IS_QUOTE = re.compile(r"^\s*>\s?(.*)$")


def _terminated(text: str) -> str:
    return text if not text or text[-1] in ".!?:;,…" else text + "."


def to_sentences(text: str, max_chars: int = 30000) -> list[Sentence]:
    """The whole pipeline: raw reply or selected text in, spoken sentences out."""
    if len(text) > max_chars:
        cut = text[:max_chars]
        end = max(cut.rfind(". "), cut.rfind("\n"))
        text = (cut[: end + 1] if end > max_chars // 2 else cut) + "\n\nThe rest is on screen."

    lines = text.replace("\r\n", "\n").split("\n")
    out: list[Sentence] = []
    paragraph: list[str] = []

    def flush():
        if paragraph:
            out.extend(split_sentences(_terminated(clean_inline(" ".join(paragraph))), PAUSE_PARAGRAPH))
            paragraph.clear()

    i = 0
    while i < len(lines):
        line = lines[i]

        fence = re.match(r"^\s*(```+|~~~+)", line)
        if fence:
            flush()
            i += 1
            while i < len(lines) and not lines[i].lstrip().startswith(fence[1]):
                i += 1
            i += 1
            out.append(Sentence("Code block skipped.", PAUSE_PARAGRAPH))
            continue

        if IS_ROW.match(line) and i + 1 < len(lines) and IS_TABLE_RULE.match(lines[i + 1]):
            flush()
            start = i
            i += 2
            while i < len(lines) and IS_ROW.match(lines[i]):
                i += 1
            out.extend(_table(lines[start:i]))
            continue

        if IS_RULE.match(line):
            flush()
            i += 1
            continue

        heading = IS_HEADING.match(line)
        if heading:
            flush()
            spoken = clean_inline(heading[1])
            if spoken:
                out.extend(split_sentences(_terminated(spoken), PAUSE_HEADING))
            i += 1
            continue

        bullet = IS_BULLET.match(line)
        numbered = IS_NUMBERED.match(line)
        if bullet or numbered:
            flush()
            body = (bullet or numbered)[1 if bullet else 2]
            i += 1
            # Lines that continue this item, indented under it.
            while i < len(lines) and lines[i].startswith((" ", "\t")) and lines[i].strip() \
                    and not IS_BULLET.match(lines[i]) and not IS_NUMBERED.match(lines[i]):
                body += " " + lines[i].strip()
                i += 1
            spoken = clean_inline(body)
            if spoken:
                prefix = f"{numbered[1]}, " if numbered else ""
                out.extend(split_sentences(_terminated(prefix + spoken), PAUSE_ITEM))
            continue

        quote = IS_QUOTE.match(line)
        if quote:
            line = quote[1]

        if line.strip():
            paragraph.append(line.strip())
        else:
            flush()
        i += 1

    flush()
    return [s for s in out if re.search(r"\w", s.text)]
