import cv2
import numpy as np

from gigsteady.register import to_matrix
from gigsteady.warp import needed_zoom


def brute(p, k, fw, fh):
    corners = np.array([[0, 0], [fw, 0], [0, fh], [fw, fh]], float)
    need = 1.0
    for q in p:
        inv = cv2.invertAffineTransform(to_matrix(q[0] * k, q[1] * k, q[2], q[3]))
        for z in np.arange(need, 1.6, 0.005):
            c = (corners - [fw / 2, fh / 2]) / z + [fw / 2, fh / 2]
            s = c @ inv[:, :2].T + inv[:, 2]
            if (s[:, 0] >= 0).all() and (s[:, 0] <= fw).all() and (s[:, 1] >= 0).all() and (s[:, 1] <= fh).all():
                need = z
                break
        else:
            need = 1.6
    return round(need + 0.005, 3)


def test_needed_zoom_matches_prototype_search():
    rng = np.random.default_rng(0)
    for scale in (0.5, 3, 15):
        p = np.column_stack([rng.normal(0, scale, 50), rng.normal(0, scale, 50), rng.normal(0, 0.003, 50), np.ones(50)])
        assert abs(needed_zoom(p, 4.0, 3840, 2160) - brute(p, 4.0, 3840, 2160)) <= 0.0051


def test_identity_zoom():
    assert needed_zoom(np.tile([0, 0, 0, 1.0], (5, 1)), 4.0, 3840, 2160) == 1.005
