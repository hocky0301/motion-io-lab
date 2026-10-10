"""Planar calibration helpers. No claims about depth or hidden mechanical states."""
from __future__ import annotations

import numpy as np


def fit_homography(image_points, plane_points, return_residual: bool = False):
    """Fit H with plane = H(image). Returns H (H[2,2] == 1), or (H, reprojection RMS in plane units)."""
    import cv2

    a = np.asarray(image_points, dtype=np.float64)
    b = np.asarray(plane_points, dtype=np.float64)
    if a.shape != b.shape or a.ndim != 2 or a.shape[1] != 2 or len(a) < 4:
        raise ValueError("Need matching (n,2) points, n>=4")
    if not np.isfinite(a).all() or not np.isfinite(b).all():
        raise ValueError("Nonfinite points")
    if np.linalg.matrix_rank(a - a.mean(axis=0)) < 2 or np.linalg.matrix_rank(b - b.mean(axis=0)) < 2:
        raise ValueError("Collinear calibration points")
    h, _ = cv2.findHomography(a, b, method=0)
    if h is None or not np.isfinite(h).all():
        raise ValueError("Ill-conditioned homography")
    if abs(h[2, 2]) < 1e-6 * np.abs(h).max():
        raise ValueError("Degenerate homography (H[2,2] ~ 0): the plane maps through infinity")
    h = h / h[2, 2]
    if not np.isfinite(h).all() or np.linalg.cond(h) > 1e12:
        raise ValueError("Ill-conditioned homography")
    if return_residual:
        return h, reprojection_rms(h, a, b)
    return h


def reprojection_rms(h, image_points, plane_points) -> float:
    err = project_points(h, image_points) - np.asarray(plane_points, dtype=float)
    return float(np.sqrt(np.mean(np.sum(err ** 2, axis=1))))


def project_points(h, points):
    h = np.asarray(h, dtype=float)
    p = np.asarray(points, dtype=float)
    if h.shape != (3, 3) or p.ndim != 2 or p.shape[1] != 2 or not np.isfinite(p).all():
        raise ValueError("Invalid projection inputs")
    hp = np.c_[p, np.ones(len(p))] @ h.T
    if (np.abs(hp[:, 2]) < 1e-9).any():
        raise ValueError("Projection at infinity")
    return hp[:, :2] / hp[:, 2, None]


def local_scalar_coordinate(points, origin, direction):
    p = np.asarray(points, dtype=float)
    o = np.asarray(origin, dtype=float)
    d = np.asarray(direction, dtype=float)
    if d.shape != (2,) or np.linalg.norm(d) < 1e-9:
        raise ValueError("Invalid direction")
    return (p - o) @ (d / np.linalg.norm(d))
