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
    return opened
