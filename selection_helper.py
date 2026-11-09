"""Prints the currently highlighted text and exits. Runs under the system Python (needs GTK).

GNOME doesn't let background programs read the selection directly on Wayland, but it shares the
primary selection with X11 clients, so this runs through XWayland like voice-dictation's clipboard helper.
"""

import os
import sys

os.environ["GDK_BACKEND"] = "x11"

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk


def got(_clipboard, text):
    if text:
        sys.stdout.write(text)
    Gtk.main_quit()


Gtk.Clipboard.get(Gdk.SELECTION_PRIMARY).request_text(got)
GLib.timeout_add(1500, Gtk.main_quit)  # a stuck app shouldn't hold this forever
Gtk.main()
