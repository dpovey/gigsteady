import numpy as np

from gigsteady.gyro.fuse import calibrate, interval_angles


def test_calibration_recovers_offset_and_axes():
    rng = np.random.default_rng(0)
    t = np.arange(0, 12, 0.0005)                                       # 2 kHz
    g = np.column_stack([np.convolve(rng.normal(0, 30, len(t)), np.ones(200) / 200, "same") for _ in range(3)])
    frame_t = 1.0 + np.arange(250) / 25
    true_off = 0.024
    X = interval_angles(t, g, frame_t + true_off)
    M = np.array([[0.0, 40.0, 0.0], [-40.0, 0.0, 0.0], [0.0, 0.0, 0.0175], [0.1, -0.2, 0.0]])  # swapped axes, scale
    Y = np.column_stack([X, np.ones(len(X))]) @ M + rng.normal(0, 0.05, (len(X), 3))
    ok = np.ones(len(Y), bool)
    r2, off, Mf = calibrate(t, g, frame_t, None, Y, ok)
    assert abs(off - true_off) < 0.0021 and r2 > 0.95
    assert np.allclose(Mf[:3, :2], M[:3, :2], atol=1.0)
