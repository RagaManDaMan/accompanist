"""Tests never touch your real phrase library (~/.accompanist): HOME is a fresh folder. And
they never open your browser (the stage screen of a `run` under test)."""
import webbrowser

import pytest


@pytest.fixture(autouse=True)
def _private_home(tmp_path_factory, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path_factory.mktemp("home")))


@pytest.fixture(autouse=True)
def _no_browser(monkeypatch):
    opened = []
    monkeypatch.setattr(webbrowser, "open", lambda url, *a, **k: opened.append(url) or True)
    from accompanist import ui
    monkeypatch.setattr(ui, "open_in_background", opened.append)
    return opened


# Settings these modules were written against, where a later default changed (each test can
# still set them itself):
# - the band as a listener: it hears your tempo and comes in by itself (start.wait, since
#   2026-10-08 the band waits for s or a tap count);
# - the guitar answers from your first phrase, in your own register (response.warmup_s and
#   response.above_you, since 2026-10-08: it listens 20 s first, and plays an octave up).
EARLIER = {
    "start": {"wait": False},
    "response": {"warmup_s": 0.0, "above_you": 0},
}
MODULES = {"test_drums": ("start",), "test_dynamics": ("start",), "test_feel": ("start",),
           "test_groove": ("start",), "test_lock": ("start",), "test_simulation": ("start",),
           "test_response": ("start", "response"), "test_piano": ("response",)}


@pytest.fixture(autouse=True)
def _earlier_defaults(request, monkeypatch):
    sections = MODULES.get(request.module.__name__.rsplit(".", 1)[-1])
    if not sections:
        return
    from accompanist import config

    original = config.from_dict

    def from_dict(d=None, *a, **k):
        d = dict(d or {})
        for sec in sections:
            merged = dict(EARLIER[sec])
            merged.update(d.get(sec) or {})
            d[sec] = merged
        return original(d, *a, **k)

    monkeypatch.setattr(config, "from_dict", from_dict)
