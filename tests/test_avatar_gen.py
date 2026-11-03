import json
import numpy as np
from PIL import Image

from mirror import avatar_gen as ag


def _img(rgb_rows):
    return Image.fromarray(np.array(rgb_rows, dtype=np.uint8), "RGB")


def test_green_background_becomes_transparent_and_subject_stays_opaque():
    green, indigo, cyan = (0, 255, 0), (40, 30, 160), (60, 230, 255)
    out = np.asarray(ag.key_out_green(_img([[green, indigo, cyan]])))
    assert out[0, 0, 3] == 0                       # background gone
    assert out[0, 1, 3] == 255 and out[0, 2, 3] == 255   # subject kept, even bright cyan


def test_edge_pixels_lose_their_green_tint():
    edge = (30, 200, 160)                          # cyan glow mixed with green
    out = np.asarray(ag.key_out_green(_img([[edge]])))
    r, g, b, a = out[0, 0]
    assert g <= max(r, b)
    assert 0 < a < 255 or a == 255


def test_anchors_land_inside_the_placed_image():
    a = ag.anchors()
    assert set(a) == {"brain", "eyes", "ears", "mouth", "heart", "hands", "feet"}
    for p in a.values():
        assert ag.RECT["x"] <= p["x"] <= ag.RECT["x"] + ag.RECT["w"]
        assert ag.RECT["y"] <= p["y"] <= ag.RECT["y"] + ag.RECT["h"]
    assert a["brain"]["y"] < a["eyes"]["y"] < a["mouth"]["y"] < a["heart"]["y"] < a["feet"]["y"]


def _map():
    def star(label, kind="pattern", policy="apply_silently"):
        return {"label": label, "kind": kind, "use_policy": policy}
    return {"regions": [
        {"id": "ears", "stars": [star("Loves live gigs"), star("Private worry", policy="internal_only"),
                                 star("Never repeats a track")]},
        {"id": "mouth", "stars": [star("Tries new restaurants")]},
        {"id": "brain", "stars": []}]}


def test_profile_brief_never_includes_internal_only_statements():
    brief = ag.profile_brief(_map())
    assert brief["ears"] == ["Loves live gigs", "Never repeats a track"]
    assert "Private worry" not in json.dumps(brief)
    assert brief["brain"] == []


def test_design_comes_from_the_model_and_green_is_removed():
    reply = json.dumps({"expression": "a wide grin", "mood": "restless and social",
                        "palette": "green and orange", "head": "a paper party hat",
                        "eyes": "star shaped sunglasses", "ears": "a festival wristband on an ear cuff",
                        "chest": "a patched denim jacket", "feet": "worn red boots",
                        "left_hand": "a ticket stub", "right_hand": "a street food skewer"})
    got = ag.design_from_profile(_map(), run=lambda p: reply)
    d = got["design"]
    assert d["head"] == "a paper party hat" and "green" not in d["palette"].lower()
    prompt = ag.build_prompt(d)
    assert "paper party hat" in prompt and "street food skewer" in prompt
    assert "#00FF00" in prompt            # the key colour is only ever the background


def test_design_falls_back_to_default_props_when_the_model_fails():
    def boom(p):
        raise RuntimeError("no network")
    got = ag.design_from_profile(_map(), run=boom)
    assert got["design"] == ag.sanitize({})
    assert got["design"]["head"] == ag.DEFAULT_DESIGN["head"]


def test_design_prompt_lists_only_non_private_things():
    p = ag.design_prompt(ag.profile_brief(_map()))
    assert "Loves live gigs" in p and "Private worry" not in p
