"""Tests never touch your real phrase library (~/.accompanist): HOME is a fresh folder."""
import pytest


@pytest.fixture(autouse=True)
def _private_home(tmp_path_factory, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path_factory.mktemp("home")))
