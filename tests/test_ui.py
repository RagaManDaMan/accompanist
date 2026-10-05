"""The stage screen: the page's requests reach the run loop, and need the run's key."""
import json
import urllib.error
import urllib.request

import pytest

from accompanist import config as c
from accompanist.controller import Controller
from accompanist.output import SafeOutput
from accompanist.ui import StageServer, stage_state


class NullPort:
    def send(self, msg):
        pass


@pytest.fixture
def stage():
    s = StageServer(18765)
    yield s
    s.close()


def call(s, path, body=None, key=True):
    req = urllib.request.Request(f"http://127.0.0.1:{s.port}{path}",
                                 data=None if body is None else json.dumps(body).encode(),
                                 headers={"X-Key": s.key if key else "nope"})
    with urllib.request.urlopen(req, timeout=2) as r:
        return r.status, r.read()


def test_the_page_and_its_state_are_served(stage):
    status, page = call(stage, "/")
    assert status == 200 and b"Count in" in page
    stage.publish({"bpm": 92.0})
    assert json.loads(call(stage, "/state")[1]) == {"bpm": 92.0}


def test_buttons_and_knobs_become_requests_for_the_run_loop(stage):
    call(stage, "/do", {"action": "break"})
    call(stage, "/set", {"key": "pad.feel", "value": 0.7})
    assert stage.take() == [("do", "break", None), ("set", "pad.feel", 0.7)]
    assert stage.take() == []


def test_without_the_key_nothing_happens(stage):
    with pytest.raises(urllib.error.HTTPError) as e:
        call(stage, "/do", {"action": "panic"}, key=False)
    assert e.value.code == 403 and stage.take() == []


def test_a_taken_port_moves_on_to_the_next(stage):
    other = StageServer(stage.port)
    try:
        assert other.port != stage.port
    finally:
        other.close()


def test_the_stage_state_has_the_feel_knobs_and_the_set():
    cfg = c.from_dict({"song": {"title": "Bay Blues"}})
    ctl = Controller(cfg, SafeOutput(NullPort()))
    s = stage_state(ctl, 0.0, {"title": "Fusion 1", "songs": ["a", "b"], "index": 1}, ["hello"])
    keys = {v["key"] for v in s["voices"]}
    assert {"pad.feel", "pad.enabled", "drums.feel"} <= keys
    assert s["title"] == "Bay Blues" and s["set"]["index"] == 1 and s["messages"] == ["hello"]
    json.dumps(s, default=str)
