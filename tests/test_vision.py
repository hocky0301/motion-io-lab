import numpy as np
import pytest

from motion_io_lab.vision import (fit_homography, local_scalar_coordinate, project_points,
                                  reprojection_rms)

SQ = np.array([[0., 0.], [1., 0.], [1., 1.], [0., 1.]])


def test_identity_projection():
    np.testing.assert_allclose(project_points(np.eye(3), SQ), SQ)


def test_known_affine_mapping():
    q = 2 * SQ + np.array([3., 4.])
    np.testing.assert_allclose(project_points(fit_homography(SQ, q), SQ), q, atol=1e-10)


def test_genuinely_projective_mapping_is_recovered():
    h_true = np.array([[1.0, 0.1, 5.0], [0.05, 1.2, -3.0], [0.002, 0.001, 1.0]])
    src = np.array([[0, 0], [100, 0], [100, 80], [0, 80], [50, 40], [20, 70]], float)
    dst = project_points(h_true, src)
    h, rms = fit_homography(src, dst, return_residual=True)
    np.testing.assert_allclose(h, h_true / h_true[2, 2], rtol=1e-6, atol=1e-8)
    assert rms < 1e-6


def test_residual_reports_noise():
    rng = np.random.default_rng(0)
    src = rng.uniform(0, 100, (12, 2))
    dst = src * 0.5 + 3 + rng.normal(0, 0.2, (12, 2))
    h, rms = fit_homography(src, dst, return_residual=True)
    assert 0.05 < rms < 0.5
    assert rms == pytest.approx(reprojection_rms(h, src, dst))


def test_collinear_rejected():
    p = np.array([[0., 0.], [1., 0.], [2., 0.], [3., 0.]])
    with pytest.raises(ValueError):
        fit_homography(p, p)


def test_homography_with_zero_h22_is_rejected():
    """A plane that maps through infinity used to come back as a matrix with entries ~3e6."""
    h_true = np.array([[1., 0.2, 3.], [0.1, 1., 2.], [0.01, 0.02, 0.0]])
    src = np.array([[10, 10], [50, 12], [55, 60], [8, 48], [30, 30], [40, 20]], float)
    with pytest.raises(ValueError, match="Degenerate|Ill-conditioned"):
        fit_homography(src, project_points(h_true, src))


def test_projection_at_infinity_rejected():
    with pytest.raises(ValueError):
        project_points(np.array([[1., 0, 0], [0, 1., 0], [0, 0, 0.]]), [[1., 2.]])


def test_nonfinite_rejected():
    p = SQ.copy()
    p[0, 0] = np.nan
    with pytest.raises(ValueError):
        fit_homography(p, SQ)


def test_direction_is_normalised_and_zero_direction_rejected():
    np.testing.assert_allclose(local_scalar_coordinate([[3., 4.]], [0., 0.], [3., 4.]), [5.])
    with pytest.raises(ValueError):
        local_scalar_coordinate([[0., 0.]], [0., 0.], [0., 0.])
