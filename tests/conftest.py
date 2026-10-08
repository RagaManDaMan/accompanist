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


# These modules test the band as a listener: it hears your tempo and comes in by itself.
# Since 2026-10-08 the default is to wait for s or a tap count (start.wait), so here the
# band is told not to wait, unless a test says otherwise.
LISTENING = {"test_drums", "test_dynamics", "test_feel", "test_groove", "test_lock",
             "test_response", "test_simulation"}


@pytest.fixture(autouse=True)
def _listening_band(request, monkeypatch):
    if request.module.__name__.rsplit(".", 1)[-1] not in LISTENING:
        return
    from accompanist import config

    original = config.from_dict

    def from_dict(d=None, *a, **k):
        d = dict(d or {})
        d.setdefault("start", {}).setdefault("wait", False)
        return original(d, *a, **k)

    monkeypatch.setattr(config, "from_dict", from_dict)
