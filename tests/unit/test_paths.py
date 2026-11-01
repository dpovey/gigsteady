import numpy as np

from gigsteady.paths import l1_path, plan


def test_l1_path_within_bounds_and_smoother():
    rng = np.random.default_rng(0)
    c = np.cumsum(rng.normal(0, 2, 200)) + np.linspace(0, 100, 200)
    b = np.full(200, 6.0)
    p = l1_path(c, b)
    assert np.all(np.abs(p - c) <= b + 1e-6)
    assert np.abs(np.diff(p, 2)).sum() < 0.2 * np.abs(np.diff(c, 2)).sum()


def test_l1_holds_still_on_pure_jitter():
    rng = np.random.default_rng(1)
    c = rng.normal(0, 1.0, 150)
    p = l1_path(c, np.full(150, 5.0))
    assert np.ptp(p) < 0.5


def test_plan_respects_budget_margin():
    rng = np.random.default_rng(2)
    n = 120
    path = np.column_stack([np.cumsum(rng.normal(0, 3, n)), np.cumsum(rng.normal(0, 3, n)),
                            np.cumsum(rng.normal(0, 0.001, n)), np.ones(n)])
    p = plan(path, 1.10, 960, 540)
    mx, my = 480 * (1 - 1 / 1.1), 270 * (1 - 1 / 1.1)
    assert np.all(np.abs(p[:, 0] - path[:, 0]) <= mx + 1e-6) and np.all(np.abs(p[:, 1] - path[:, 1]) <= my + 1e-6)
