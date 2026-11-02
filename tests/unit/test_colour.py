import numpy as np

from gigsteady.colour import rgb48_to_yuv422p10, yuv422p10_to_rgb48


def test_rgb_yuv_roundtrip_is_unbiased():
    rng = np.random.default_rng(0)
    # smooth image (chroma subsampling is lossless on smooth content), Log-like mid tones
    h, w = 64, 128
    x = np.linspace(0, 1, w)[None, :, None]
    yy = np.linspace(0, 1, h)[:, None, None]
    rgb = (0.2 + 0.5 * x * np.array([1.0, 0.8, 0.6]) + 0.2 * yy * np.array([0.3, 0.6, 0.9])) * 65535
    rgb = np.clip(rgb + rng.normal(0, 20, rgb.shape), 0, 65535).astype(np.uint16)
    buf = rgb48_to_yuv422p10(rgb)
    back = yuv422p10_to_rgb48(buf, w, h, full=False)
    d = back.astype(float) - rgb
    assert abs(d.mean()) < 20          # < 0.1 of a 10-bit code, in 16-bit units
    assert np.percentile(abs(d), 99) < 200


def test_known_values_video_range():
    rgb = np.zeros((2, 4, 3), np.uint16)
    rgb[:, :2] = 65535                    # white
    a = np.frombuffer(rgb48_to_yuv422p10(rgb), "<u2")
    y = a[:8].reshape(2, 4)
    assert y[0, 0] == 940 and y[0, 3] == 64
    assert np.all(a[8:] == 512)           # neutral chroma
