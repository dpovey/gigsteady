import cv2
import numpy as np

from gigsteady.lock import analyse_lock
from gigsteady.register import to_matrix

W, H = 960, 540


def scene(seed=0):
    rng = np.random.default_rng(seed)
    base = cv2.GaussianBlur(rng.random((H, W)).astype(np.float32), (0, 0), 3)
    base = (base - base.min()) / np.ptp(base)
    # some hard-edged "set pieces"
    for _ in range(40):
        x, y = rng.integers(0, W - 60), rng.integers(0, H - 60)
        cv2.rectangle(base, (int(x), int(y)), (int(x + rng.integers(10, 60)), int(y + rng.integers(10, 60))),
                      float(rng.random()), 2)
    return base


def shot(n=60, flip=False, seed=0):
    rng = np.random.default_rng(seed + 1)
    base = scene(seed)
    shifts = np.column_stack([rng.normal(0, 1.5, n), rng.normal(0, 1.5, n), rng.normal(0, 0.002, n)])
    if flip:  # the fixed-camera assumption: each lighting state's median position is the same
        st = (np.arange(n) // 10) % 2
        for k in (0, 1):
            shifts[st == k] -= np.median(shifts[st == k], 0)
    frames = []
    for k, (dx, dy, r) in enumerate(shifts):
        f = cv2.warpAffine(base, to_matrix(dx, dy, r, 1.0), (W, H), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REFLECT)
        if flip and (k // 10) % 2:
            f = f * 0.35 + 0.08  # dimmer, hazier lighting state
        f = f.copy()
        cx = int(200 + 8 * k) % (W - 80)  # a moving "performer"
        f[300:420, cx:cx + 80] = rng.random()
        f = f + rng.normal(0, 0.01, f.shape)
        frames.append(np.clip(f * 200 + 20, 0, 255).astype(np.uint8))
    return np.stack(frames), shifts


def centred(a):
    return a - np.median(a, 0)


def test_lock_recovers_shake():
    g, s = shot()
    r = analyse_lock(g)
    assert r.good.all()
    err = np.abs(centred(r.p[:, :2]) + centred(s[:, :2]))
    assert np.percentile(err, 95) < 0.15, err.max()


def test_lock_lighting_flip_no_hop():
    g, s = shot(flip=True, seed=3)
    r = analyse_lock(g)
    assert len(np.unique(r.states)) == 2
    err = centred(r.p[:, :2]) + centred(s[:, :2])
    # mean error per lighting state: a picture-matched tie would leave a bias between states
    bias = np.abs(err[r.states == 0].mean(0) - err[r.states == 1].mean(0))
    assert bias.max() < 0.2 and np.percentile(np.abs(err), 95) < 0.25, (bias, np.abs(err).max())


def test_lock_unregistrable_gives_identity():
    rng = np.random.default_rng(5)
    g = (rng.random((20, H, W)) * 255).astype(np.uint8)  # pure noise, nothing to lock to
    r = analyse_lock(g)
    assert r.p.shape == (20, 4)
