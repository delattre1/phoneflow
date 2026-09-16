import json

import pytest

from pf_api.agent import AgentLoop
from pf_api.driver import DriverError, FakeDriver


class Fn:
    def __init__(self, name, args):
        self.name = name
        self.arguments = json.dumps(args)


class Call:
    def __init__(self, name, args):
        self.id = f"call_{name}"
        self.type = "function"
        self.function = Fn(name, args)


class Msg:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class Reply:
    def __init__(self, message):
        self.choices = [type("C", (), {"message": message})()]


def tool(name, **args):
    return Reply(Msg(tool_calls=[Call(name, args)]))


def text_reply(text):
    return Reply(Msg(content=text))


class FakeClient:
    """Replays scripted replies over the OpenAI-compatible surface."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []
        self.chat = type("Chat", (), {"completions": self})()

    def create(self, **kw):
        self.requests.append(kw)
        return self.replies.pop(0)


ITEMS = [{"t": "Subscriptions", "x": 200, "y": 300}, {"t": "Search", "x": 260, "y": 120}]


def _loop(replies, driver=None, **kw):
    drv = driver or FakeDriver(items=ITEMS)
    kw.setdefault("grounder", None)
    return AgentLoop(drv, client=FakeClient(replies), **kw), drv


def test_agent_taps_the_item_the_model_picked():
    loop, drv = _loop([tool("tap", item=1, why="open subs"), tool("done", summary="ok")])
    out = loop.run("open subscriptions")
    assert out["status"] == "succeeded"
    assert ("tap_point", 200, 300) in drv.calls


def test_agent_reports_done_summary():
    loop, _ = _loop([tool("done", summary="found 3 videos")])
    out = loop.run("count videos")
    assert out["status"] == "succeeded"
    assert out["message"] == "found 3 videos"
    assert out["used"] == 1


def test_agent_give_up_is_not_a_failure():
    loop, _ = _loop([tool("give_up", reason="password prompt")])
    out = loop.run("log in")
    assert out["status"] == "given_up"
    assert "password" in out["message"]


def test_agent_stops_at_the_step_budget():
    # Distinct actions: identical ones are refused by the no-op guard instead.
    replies = [tool("swipe", direction=d, why="look further") for d in ("up", "down", "left")]
    loop, drv = _loop(replies, max_steps=3)
    out = loop.run("scroll forever")
    assert out["status"] == "out_of_steps"
    assert len([c for c in drv.calls if c[0] == "swipe"]) == 3


def test_agent_rejects_an_item_number_that_is_not_on_the_list():
    # The model must be told the index was bad rather than a click being sent.
    loop, drv = _loop([tool("tap", item=99, why="guess"), tool("done", summary="stopped")])
    loop.run("tap something")
    assert not [c for c in drv.calls if c[0] == "tap_point"]


def test_agent_feeds_a_driver_refusal_back_to_the_model():
    class Refusing(FakeDriver):
        def tap_point(self, x, y):
            raise DriverError("tap_out_of_bounds", "off the phone")

    loop, _ = _loop(
        [tool("tap", item=1, why="try"), tool("done", summary="recovered")],
        driver=Refusing(items=ITEMS),
    )
    out = loop.run("tap")
    assert out["status"] == "succeeded"
    results = [
        m for req in loop.client.requests for m in req["messages"] if m.get("role") == "tool"
    ]
    assert results and "tap_out_of_bounds" in results[0]["content"]


def test_agent_sends_the_numbered_listing_and_the_downscaled_frame():
    loop, drv = _loop([tool("done", summary="ok")])
    loop.run("look")
    msgs = loop.client.requests[0]["messages"]
    assert msgs[0]["role"] == "system"
    first = msgs[1]["content"]
    assert first[0]["type"] == "image_url"
    assert "1. Subscriptions" in first[1]["text"] and "2. Search" in first[1]["text"]
    # The big capture is never what gets sent to the model.
    assert ("frame_for_model",) in drv.calls


def test_agent_retries_an_empty_reply_before_stalling():
    # A half-rendered screen produces an empty reply; one of those must not end
    # the run, three in a row must.
    loop, _ = _loop([text_reply(""), text_reply(""), tool("done", summary="settled")])
    assert loop.run("wait it out")["status"] == "succeeded"

    loop2, _ = _loop([text_reply("I am not sure.") for _ in range(3)])
    out = loop2.run("do something")
    assert out["status"] == "stalled"
    assert "not sure" in out["message"]


def test_agent_wait_tool_does_not_touch_the_phone():
    loop, drv = _loop([tool("wait", why="loading"), tool("done", summary="ok")])
    loop.run("be patient")
    assert not [c for c in drv.calls if c[0] in ("tap_point", "swipe", "type_text")]


def test_agent_records_every_step_it_took():
    loop, _ = _loop(
        [tool("swipe", direction="up", why="scroll"), tool("done", summary="done")]
    )
    out = loop.run("scroll then stop")
    assert [s["action"] for s in out["steps"]] == ["swipe", "done"]


def test_agent_refuses_to_tap_a_pay_or_submit_control():
    # The schema guard is validation-time and only covers canvas-drawn taps, so
    # the same rule has to hold for a target the model picks at runtime.
    loop, drv = _loop(
        [tool("tap", item=1, why="checkout"), tool("give_up", reason="payment screen")],
        driver=FakeDriver(items=[{"t": "Confirmar pagamento", "x": 200, "y": 300}]),
    )
    out = loop.run("buy something")
    assert out["status"] == "given_up"
    assert not [c for c in drv.calls if c[0] == "tap_point"]
    refusal = [m for r in loop.client.requests for m in r["messages"] if m.get("role") == "tool"]
    assert refusal and "Refused" in refusal[0]["content"]


def test_agent_stops_waiting_after_a_few_tries():
    replies = [tool("wait", why="loading") for _ in range(5)] + [tool("done", summary="ok")]
    loop, _ = _loop(replies, max_steps=8)
    loop.run("be patient")
    # The 5th wait must be told to stop waiting rather than sleeping again.
    results = [m["content"] for r in loop.client.requests for m in r["messages"] if m.get("role") == "tool"]
    assert any("Waiting again will not help" in c for c in results)


def test_agent_names_truncation_when_a_reply_carries_no_action():
    # A reply truncated mid-reasoning looks identical to a model that chose
    # nothing; the stall must say which, or the cause is unfindable.
    class Truncated(Reply):
        def __init__(self):
            super().__init__(Msg(content=""))
            self.choices[0].finish_reason = "length"

    loop, _ = _loop([Truncated() for _ in range(3)])
    out = loop.run("do something")
    assert out["status"] == "stalled"
    assert "finish_reason=length" in out["message"]


def test_agent_is_told_when_the_screen_did_not_change():
    # The model cannot see that its action achieved nothing; left to guess it
    # sends the same one again.
    loop, _ = _loop([tool("swipe", direction="up", why="scroll"), tool("done", summary="ok")])
    loop.run("scroll")
    texts = [
        b["text"]
        for r in loop.client.requests
        for m in r["messages"]
        if isinstance(m.get("content"), list)
        for b in m["content"]
        if b.get("type") == "text"
    ]
    assert any("screen did not change" in t for t in texts)


def test_agent_refuses_to_repeat_an_action_that_did_nothing():
    replies = [tool("swipe", direction="up", why="a"), tool("swipe", direction="up", why="a"),
               tool("done", summary="ok")]
    loop, drv = _loop(replies)
    loop.run("scroll")
    # Executed once, refused the second time.
    assert len([c for c in drv.calls if c[0] == "swipe"]) == 1
    results = [m["content"] for r in loop.client.requests for m in r["messages"] if m.get("role") == "tool"]
    assert any("Refused" in c for c in results)


def test_agent_clock_alone_does_not_count_as_a_changed_screen():
    assert AgentLoop._signature([{"t": "23:30"}, {"t": "Home"}]) == \
           AgentLoop._signature([{"t": "23:31"}, {"t": "Home"}])


def test_agent_swipe_can_be_anchored_to_an_item():
    # A centre swipe never touches a row of chips under the header.
    loop, drv = _loop([tool("swipe", direction="left", at_item=2, why="chips"),
                       tool("done", summary="ok")])
    loop.run("scroll the chips")
    assert ("swipe_from", 260, 120, "left") in drv.calls


def test_agent_system_buttons_reach_the_driver():
    loop, drv = _loop([tool("system_button", button="back", why="leave"),
                       tool("system_button", button="home", why="reset"),
                       tool("done", summary="ok")])
    loop.run("navigate")
    assert ("back",) in drv.calls and ("system_key", "home") in drv.calls


class GroundReply:
    def __init__(self, text):
        self.choices = [type("C", (), {"message": type("M", (), {"content": text})()})()]


def test_grounder_maps_icon_boxes_onto_window_coordinates():
    from pf_api.agent import IconGrounder
    # No anchors: falls back to the 0-1000 grid. Centre (450, 90) is 45% across
    # and 9% down a 300x600 window at (100,100) -> (235, 154).
    client = FakeClient([GroundReply('[{"label":"search","bbox_2d":[400,60,500,120]}]')])
    g = IconGrounder(client, model="qwen")
    items = g.find(b"jpg", {"pos": (100, 100), "size": (300, 600), "px_w": 900, "px_h": 1800})
    assert items == [{"t": "[icon] search", "x": 235.0, "y": 154.0}]


def test_grounder_infers_a_pixel_space_model_from_the_anchors():
    from pf_api.agent import IconGrounder
    # Anchor "Home" truly sits at (250, 400): 50% across, 50% down. A model
    # answering in pixels puts it at (450, 900) of a 900x1800 crop; read as a
    # 0-1000 grid that would land at (235, 640) — 260pt out. The anchor is what
    # tells the two apart, so the icon must map by pixels too.
    reply = GroundReply('[{"label":"Home","bbox_2d":[440,880,460,920]},'
                        '{"label":"bell","bbox_2d":[880,180,900,220]}]')
    g = IconGrounder(FakeClient([reply]), model="flash")
    geom = {"pos": (100, 100), "size": (300, 600), "px_w": 900, "px_h": 1800, "titlebar": 0}
    items = g.find(b"jpg", geom, [{"t": "Home", "x": 250, "y": 400}])
    assert [i["t"] for i in items] == ["[icon] bell"]        # the anchor itself is dropped
    assert abs(items[0]["x"] - (100 + 890 / 900 * 300)) < 1  # pixel space, not 0-1000
    assert abs(items[0]["y"] - (100 + 200 / 1800 * 600)) < 1


def test_grounder_survives_one_malformed_entry():
    from pf_api.agent import IconGrounder
    reply = GroundReply('[{"label":"bad","bbox_2d":[1,2]}, {"oops": , }, '
                        '{"label":"search","bbox_2d":[400,60,500,120]}]')
    items = IconGrounder(FakeClient([reply]), model="qwen").find(
        b"jpg", {"pos": (100, 100), "size": (300, 600), "px_w": 900, "px_h": 1800})
    assert [i["t"] for i in items] == ["[icon] search"]


def test_grounder_failure_yields_no_icons_not_an_error():
    from pf_api.agent import IconGrounder

    class Broken:
        chat = type("Chat", (), {"completions": type("Cmp", (), {
            "create": staticmethod(lambda **kw: (_ for _ in ()).throw(RuntimeError("down")))})()})()

    assert IconGrounder(Broken(), model="qwen").find(b"jpg", {}) == []
    assert IconGrounder(FakeClient([GroundReply("not json")]), model="qwen").find(b"jpg", {}) == []


def test_observe_appends_icons_after_text_and_they_are_tappable():
    from pf_api.agent import IconGrounder
    ground = FakeClient([GroundReply('[{"label":"bell","bbox_2d":[800,60,880,120]}]')])
    drv = FakeDriver(items=ITEMS)
    loop = AgentLoop(drv, client=FakeClient([tool("tap", item=3, why="bell"), tool("done", summary="ok")]),
                     grounder=IconGrounder(ground, model="qwen"))
    loop.run("ring the bell")
    # item 3 is the icon (after the two OCR items), tapped at its mapped point.
    taps = [c for c in drv.calls if c[0] == "tap_point"]
    assert taps and taps[0][1] == 100 + 840 / 1000 * 300 and taps[0][2] == 100 + 90 / 1000 * 600


def test_signature_ignores_icon_entries_which_vary_between_frames():
    # Same screen, grounder named/counted icons differently: must compare equal.
    a = [{"t": "Home"}, {"t": "[icon] search"}, {"t": "[icon] more_options"}]
    b = [{"t": "Home"}, {"t": "[icon] Search Icon"}]
    assert AgentLoop._signature(a) == AgentLoop._signature(b)


def test_noop_guard_catches_the_same_target_under_a_new_item_number():
    # Frame 1 lists the magnifier as item 1, frame 2 as item 2: same target.
    class Shuffling(FakeDriver):
        n = 0
        def ocr_items(self, frame):
            self.n += 1
            icon = {"t": "[icon] search", "x": 250, "y": 140}
            return [icon, {"t": "Home", "x": 200, "y": 400}] if self.n == 1 else \
                   [{"t": "Home", "x": 200, "y": 400}, icon]
    drv = Shuffling()
    loop = AgentLoop(drv, client=FakeClient([tool("tap", item=1, why="s"), tool("tap", item=2, why="s"),
                                            tool("done", summary="ok")]), grounder=None)
    loop.run("search")
    assert len([c for c in drv.calls if c[0] == "tap_point"]) == 1


def test_grounder_drops_icons_in_the_mac_title_bar():
    from pf_api.agent import IconGrounder
    geom = {"pos": (100, 100), "size": (300, 600), "px_w": 900, "px_h": 1800, "titlebar": 28}
    reply = GroundReply('[{"label":"red_close","bbox_2d":[10,10,40,40]},'
                        '{"label":"search","bbox_2d":[400,300,500,360]}]')
    items = IconGrounder(FakeClient([reply]), model="qwen").find(b"jpg", geom)
    assert [i["t"] for i in items] == ["[icon] search"]


def test_only_the_newest_screenshot_is_kept_in_the_conversation():
    # Each turn appends a frame; by step ten the request would carry ten images
    # of screens that no longer exist.
    loop, _ = _loop([tool("swipe", direction="up", why="a"),
                     tool("swipe", direction="down", why="b"),
                     tool("done", summary="ok")])
    loop.run("scroll about")
    last = loop.client.requests[-1]["messages"]
    images = [c for m in last if isinstance(m.get("content"), list)
              for c in m["content"] if c.get("type") == "image_url"]
    assert len(images) == 1
    # the earlier turns keep their text, so the history is not lost
    texts = [c["text"] for m in last if isinstance(m.get("content"), list)
             for c in m["content"] if c.get("type") == "text"]
    assert len(texts) >= 3


def test_grounder_reuses_icons_while_the_screen_text_is_unchanged():
    # The JPEG differs every frame (clock, video, compression), so an image hash
    # never hits and every step would pay for grounding again.
    from pf_api.agent import IconGrounder
    client = FakeClient([GroundReply('[{"label":"search","bbox_2d":[400,60,500,120]}]')])
    g = IconGrounder(client, model="qwen")
    geom = {"pos": (100, 100), "size": (300, 600), "px_w": 900, "px_h": 1800}
    screen = [{"t": "Home", "x": 1, "y": 1}, {"t": "23:30", "x": 1, "y": 1}]
    first = g.find(b"jpeg-frame-1", geom, screen)
    again = g.find(b"jpeg-frame-2", geom, [{"t": "Home", "x": 1, "y": 1}, {"t": "23:31", "x": 1, "y": 1}])
    assert first == again
    assert len(client.requests) == 1            # only the clock moved: no second call
    g.find(b"jpeg-frame-3", geom, [{"t": "Settings", "x": 1, "y": 1}])
    assert len(client.requests) == 2            # real change: grounds again


def test_grounder_reports_a_truncated_reply_rather_than_pretending_there_are_no_icons(capsys):
    from pf_api.agent import IconGrounder

    class Truncated:
        class _C:
            @staticmethod
            def create(**kw):
                msg = type("M", (), {"content": ""})()
                return type("R", (), {"choices": [type("C", (), {"message": msg, "finish_reason": "length"})()]})()
        chat = type("Chat", (), {"completions": _C()})()

    assert IconGrounder(Truncated(), model="flash").find(b"jpg", {}) == []
    assert "finish_reason=length" in capsys.readouterr().out


def test_planner_and_grounder_default_to_different_models():
    # Planning is judgement and wants speed; grounding is precision, where the
    # fast model measured 76pt off against a 44pt tap target.
    import pf_api.agent as a
    assert a.MODEL != a.GROUNDER_MODEL
