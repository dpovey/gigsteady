"""Crop-constrained camera path planning (L1 optimal paths, Grundmann, Kwatra & Essa, CVPR 2011).

Given the raw camera path c[t] (one parameter: x, y or roll), find the smooth path p[t] that minimises
w1*sum|p'| + w2*sum|p''| + w3*sum|p'''| subject to |p[t] - c[t]| <= bound[t]. L1 norms make the answer piecewise:
held still (p' = 0), constant-speed pans (p'' = 0) and smooth eases between them. The bound is what the crop budget
allows, so the path never needs more crop than the budget. Solved as a linear program with HiGHS (scipy).
"""
import numpy as np
from scipy import sparse
from scipy.optimize import linprog

W1, W2, W3 = 10.0, 1.0, 100.0  # Grundmann et al.'s weights


def _diff(n: int, k: int) -> sparse.csr_matrix:
    d = sparse.identity(n, format="csr")
    for _ in range(k):
        d = (d[1:] - d[:-1]).tocsr()
    return d


def l1_path(c: np.ndarray, bound: np.ndarray, w=(W1, W2, W3)) -> np.ndarray:
    """Smooth path within c +- bound (both length n). Falls back to c if the LP fails."""
    n = len(c)
    if n < 4:
        return c.copy()
    Ds = [_diff(n, k) for k in (1, 2, 3)]
    m = [D.shape[0] for D in Ds]
    nv = n + sum(m)
    cost = np.concatenate([np.zeros(n)] + [np.full(mk, wk) for mk, wk in zip(m, w)])
    rows, off = [], n
    for D, mk in zip(Ds, m):
        E = sparse.csr_matrix((np.ones(mk), (np.arange(mk), off + np.arange(mk))), shape=(mk, nv))
        Dp = sparse.hstack([D, sparse.csr_matrix((mk, nv - n))])
        rows += [Dp - E, -Dp - E]
        off += mk
    A = sparse.vstack(rows).tocsc()
    bounds = [(ci - bi, ci + bi) for ci, bi in zip(c, bound)] + [(0, None)] * sum(m)
    res = linprog(cost, A_ub=A, b_ub=np.zeros(A.shape[0]), bounds=bounds, method="highs")
    return res.x[:n] if res.status == 0 else c.copy()


def plan(path: np.ndarray, budget: float, w: int, h: int, roll_share: float = 0.25) -> np.ndarray:
    """path: (n, 4) raw camera path (x, y, roll rad, scale) in analysis px. Returns the smooth path.

    Crop budget z leaves a centred margin of w/2*(1-1/z) and h/2*(1-1/z). A roll r swings the crop's corners by
    about r*h/2/z horizontally and r*w/2/z vertically, so roll gets `roll_share` of the vertical margin first and
    x/y get what remains at each frame.
    """
    mx, my = w / 2 * (1 - 1 / budget), h / 2 * (1 - 1 / budget)
    rmax = roll_share * my / (w / 2 / budget)
    n = len(path)
    p = path.copy()
    p[:, 2] = l1_path(path[:, 2], np.full(n, rmax))
    r = np.abs(p[:, 2] - path[:, 2])
    p[:, 0] = l1_path(path[:, 0], np.maximum(mx - r * h / 2 / budget, 0.0))
    p[:, 1] = l1_path(path[:, 1], np.maximum(my - r * w / 2 / budget, 0.0))
    p[:, 3] = path[:, 3]
    return p
