from PIL import Image

from zurich_air.rendering.symbols import ARROW_SIZE, arrow


def test_arrows_point_in_eight_directions():
    masks = [arrow(octant) for octant in range(8)]
    assert len({m.tobytes() for m in masks}) == 8
    for octant, mask in enumerate(masks):
        assert mask.size == (ARROW_SIZE, ARROW_SIZE)
        turned = mask.transpose(Image.Transpose.ROTATE_270)  # a quarter turn clockwise
        assert turned.tobytes() == masks[(octant + 2) % 8].tobytes()
    assert masks[2].getpixel((ARROW_SIZE - 1, ARROW_SIZE // 2))  # E: tip on the right
    assert masks[1].getpixel((ARROW_SIZE - 1, 0))  # NE: tip top right
