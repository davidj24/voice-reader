"""Run with: python3 -m unittest discover tests (from the voice-reader folder)."""

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from textclean import clean_inline, to_sentences


class Inline(unittest.TestCase):
    def check(self, cases):
        for given, want in cases:
            with self.subTest(given):
                self.assertEqual(clean_inline(given), want)

    def test_units(self):
        self.check([
            ("5 km/h", "5 kilometers per hour"),
            ("about 228k tokens", "about 228 thousand tokens"),
            ("it uses 0.34 GB", "it uses 0.34 gigabytes"),
            ("1 GB left", "1 gigabyte left"),
            ("a 1h cache", "a 1 hour cache"),
            ("a 5m cache", "a 5 minutes cache"),
            ("wait 200ms", "wait 200 milliseconds"),
            ("at 2.4 GHz", "at 2.4 gigahertz"),
            ("Qwen 1.7B", "Qwen 1.7 billion"),
        ])

    def test_money(self):
        self.check([
            ("$0.34", "34 cents"),
            ("$1.50", "1 dollar and 50 cents"),
            ("$5", "5 dollars"),
            ("$1.5k", "1.5 thousand dollars"),
            ("$1,200 total", "1,200 dollars total"),
            ("$5 million", "5 million dollars"),
        ])

    def test_symbols(self):
        self.check([
            ("5–10 files", "5 to 10 files"),
            ("10-15 files", "10 to 15 files"),
            ("20× more", "20 times more"),
            ("~5 files", "about 5 files"),
            ("≈0.2%", "about 0.2%"),
            ("see #123", "see number 123"),
            ("cache → disk", "cache to disk"),
            ("red & blue", "red and blue"),
            ("fast — very fast", "fast, very fast"),
        ])

    def test_abbreviations(self):
        self.check([
            ("use a tool, e.g. curl", "use a tool, for example curl"),
            ("the base, i.e. the root", "the base, that is the root"),
            ("Kokoro vs. Piper", "Kokoro versus Piper"),
            ("cats, dogs, etc.", "cats, dogs, et cetera."),
            ("w/o the cache", "without the cache"),
        ])

    def test_code_names(self):
        self.check([
            ("edit `/home/me/projects/app/main.py:42` now", "edit main dot P Y, line 42 now"),
            ("in register.tsx", "in register dot T S X"),
            ("run /cache show", "run slash cache show"),
            ("set auto_read on", "set auto read on"),
            ("pass --scope user", "pass scope user"),
            ("see https://github.com/a/b for more", "see link to github dot com for more"),
            ("on/off", "on or off"),
        ])

    def test_dates_and_versions(self):
        self.check([
            ("on 2026-11-09", "on November 9, 2026"),
            ("at 3:40pm", "at 3 40 P M"),
            ("v1.2.3", "version 1 point 2 point 3"),
        ])

    def test_prose_is_untouched(self):
        text = "The cat sat on the mat, and that was fine."
        self.assertEqual(clean_inline(text), text)


class Blocks(unittest.TestCase):
    def texts(self, source):
        return [s.text for s in to_sentences(source)]

    def test_markdown_list(self):
        self.assertEqual(self.texts("Steps:\n\n- **Open** the app\n- Click Save\n\n1. First thing\n2. Second thing"),
                         ["Steps:", "Open the app.", "Click Save.", "1, First thing.", "2, Second thing."])

    def test_code_block_is_skipped(self):
        self.assertEqual(self.texts("Run this:\n\n```bash\nrm -rf x\n```\n\nThen stop."),
                         ["Run this:", "Code block skipped.", "Then stop."])

    def test_heading_gets_a_long_pause(self):
        out = to_sentences("## Setup\n\nA sentence goes here for testing.")
        self.assertEqual(out[0].text, "Setup.")
        self.assertGreater(out[0].pause, out[1].pause - 0.2)

    def test_table_reads_by_row(self):
        out = self.texts("| Model | Size |\n| --- | --- |\n| Kokoro | 0.34 GB |\n| Piper | 0.11 GB |")
        self.assertEqual(out, ["Kokoro. Size: 0.34 gigabytes.", "Piper. Size: 0.11 gigabytes."])

    def test_long_table_is_cut_short(self):
        rows = "\n".join(f"| name{i} | {i} |" for i in range(20))
        self.assertEqual(self.texts(f"| A | B |\n| --- | --- |\n{rows}")[-1], "And 12 more rows on screen.")

    def test_sentences_split_but_decimals_do_not(self):
        self.assertEqual(self.texts("It costs 0.34 today. Tomorrow it may cost more than that."),
                         ["It costs 0.34 today.", "Tomorrow it may cost more than that."])

    def test_plain_style_english_passes_through(self):
        # ASD-STE100 or ELI5 replies are short plain sentences, so they stay as they are.
        source = "Do not open the valve. Make sure that the pump is off. Then remove the cover."
        self.assertEqual(self.texts(source), ["Do not open the valve.", "Make sure that the pump is off.",
                                              "Then remove the cover."])

    def test_very_long_sentence_is_split(self):
        source = ", ".join(["a phrase that goes on for a while"] * 30) + "."
        self.assertTrue(all(len(t) <= 330 for t in self.texts(source)))

    def test_only_symbols_gives_nothing(self):
        self.assertEqual(self.texts("--- \n\n***"), [])


if __name__ == "__main__":
    unittest.main()
