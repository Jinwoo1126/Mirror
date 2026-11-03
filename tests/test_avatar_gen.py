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
