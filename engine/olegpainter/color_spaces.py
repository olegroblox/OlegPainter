from __future__ import annotations

import numpy as np

_D65_WHITE = np.array([95.047, 100.0, 108.883], dtype=np.float64)
_SRGB_TO_XYZ = np.array(
    [
        [0.4124564, 0.3575761, 0.1804375],
        [0.2126729, 0.7151522, 0.0721750],
        [0.0193339, 0.1191920, 0.9503041],
    ],
    dtype=np.float64,
)
_XYZ_TO_SRGB = np.array(
    [
        [3.2404542, -1.5371385, -0.4985314],
        [-0.9692660, 1.8760108, 0.0415560],
        [0.0556434, -0.2040259, 1.0572252],
    ],
    dtype=np.float64,
)
_RGB_TO_LMS = np.array(
    [
        [0.4122214708, 0.5363325363, 0.0514459929],
        [0.2119034982, 0.6806995451, 0.1073969566],
        [0.0883024619, 0.2817188376, 0.6299787005],
    ],
    dtype=np.float64,
)
_LMS_TO_OKLAB = np.array(
    [
        [0.2104542553, 0.7936177850, -0.0040720468],
        [1.9779984951, -2.4285922050, 0.4505937099],
        [0.0259040371, 0.7827717662, -0.8086757660],
    ],
    dtype=np.float64,
)
_OKLAB_TO_LMS = np.array(
    [
        [1.0, 0.3963377774, 0.2158037573],
        [1.0, -0.1055613458, -0.0638541728],
        [1.0, -0.0894841775, -1.2914855480],
    ],
    dtype=np.float64,
)
_LMS_TO_RGB = np.array(
    [
        [4.0767416621, -3.3077115913, 0.2309699292],
        [-1.2684380046, 2.6097574011, -0.3413193965],
        [-0.0041960863, -0.7034186147, 1.7076147010],
    ],
    dtype=np.float64,
)


def _as_rgb_float(rgb: np.ndarray | list | tuple) -> np.ndarray:
    arr = np.asarray(rgb, dtype=np.float64)
    if arr.shape[-1] != 3:
        raise ValueError("Expected RGB-like array with last dimension == 3")
    return np.clip(arr, 0.0, 255.0)


def _srgb_to_linear(rgb: np.ndarray) -> np.ndarray:
    rgb = rgb / 255.0
    return np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)


def _linear_to_srgb(rgb_linear: np.ndarray) -> np.ndarray:
    rgb_linear = np.clip(rgb_linear, 0.0, None)
    return np.where(
        rgb_linear <= 0.0031308,
        rgb_linear * 12.92,
        1.055 * np.power(rgb_linear, 1.0 / 2.4) - 0.055,
    )


def rgb_to_xyz(rgb: np.ndarray | list | tuple) -> np.ndarray:
    rgb = _as_rgb_float(rgb)
    linear = _srgb_to_linear(rgb)
    xyz = np.tensordot(linear, _SRGB_TO_XYZ.T, axes=1)
    return xyz * 100.0


def xyz_to_rgb(xyz: np.ndarray | list | tuple) -> np.ndarray:
    xyz = np.asarray(xyz, dtype=np.float64)
    if xyz.shape[-1] != 3:
        raise ValueError("Expected XYZ-like array with last dimension == 3")
    linear = np.tensordot(xyz / 100.0, _XYZ_TO_SRGB.T, axes=1)
    srgb = _linear_to_srgb(linear)
    return np.clip(np.rint(srgb * 255.0), 0, 255).astype(np.uint8)


def rgb_to_cielab_numpy(rgb: np.ndarray | list | tuple) -> np.ndarray:
    xyz = rgb_to_xyz(rgb)
    xyz_n = xyz / _D65_WHITE
    delta = 6.0 / 29.0
    delta3 = delta ** 3
    f_xyz = np.where(xyz_n > delta3, np.cbrt(xyz_n), xyz_n / (3.0 * delta * delta) + 4.0 / 29.0)
    l = 116.0 * f_xyz[..., 1] - 16.0
    a = 500.0 * (f_xyz[..., 0] - f_xyz[..., 1])
    b = 200.0 * (f_xyz[..., 1] - f_xyz[..., 2])
    return np.stack([l, a, b], axis=-1)


def cielab_to_rgb_numpy(lab: np.ndarray | list | tuple) -> np.ndarray:
    lab = np.asarray(lab, dtype=np.float64)
    if lab.shape[-1] != 3:
        raise ValueError("Expected LAB-like array with last dimension == 3")
    fy = (lab[..., 0] + 16.0) / 116.0
    fx = fy + lab[..., 1] / 500.0
    fz = fy - lab[..., 2] / 200.0
    delta = 6.0 / 29.0

    def _inv_f(t: np.ndarray) -> np.ndarray:
        return np.where(t > delta, t ** 3, 3.0 * delta * delta * (t - 4.0 / 29.0))

    x = _inv_f(fx) * _D65_WHITE[0]
    y = _inv_f(fy) * _D65_WHITE[1]
    z = _inv_f(fz) * _D65_WHITE[2]
    return xyz_to_rgb(np.stack([x, y, z], axis=-1))


def rgb_to_oklab_numpy(rgb: np.ndarray | list | tuple) -> np.ndarray:
    rgb = _as_rgb_float(rgb)
    linear = _srgb_to_linear(rgb)
    lms = np.tensordot(linear, _RGB_TO_LMS.T, axes=1)
    lms_cbrt = np.cbrt(np.clip(lms, 0.0, None))
    oklab = np.tensordot(lms_cbrt, _LMS_TO_OKLAB.T, axes=1)
    return oklab


def oklab_to_rgb_numpy(oklab: np.ndarray | list | tuple) -> np.ndarray:
    oklab = np.asarray(oklab, dtype=np.float64)
    if oklab.shape[-1] != 3:
        raise ValueError("Expected OKLab-like array with last dimension == 3")
    l_ = oklab[..., 0]
    a_ = oklab[..., 1]
    b_ = oklab[..., 2]
    lms = np.tensordot(np.stack([l_, a_, b_], axis=-1), _OKLAB_TO_LMS.T, axes=1)
    lms = np.power(np.clip(lms, 0.0, None), 3.0)
    linear_rgb = np.tensordot(lms, _LMS_TO_RGB.T, axes=1)
    srgb = _linear_to_srgb(linear_rgb)
    return np.clip(np.rint(srgb * 255.0), 0, 255).astype(np.uint8)


def perceptual_distance(
    color_a: np.ndarray | list | tuple,
    color_b: np.ndarray | list | tuple,
    *,
    space: str = "lab",
    input_space: str = "rgb",
) -> np.ndarray:
    a = np.asarray(color_a, dtype=np.float64)
    b = np.asarray(color_b, dtype=np.float64)
    input_space = input_space.lower()
    space = space.lower()
    if input_space == "rgb":
        if space == "oklab":
            diff = rgb_to_oklab_numpy(a) - rgb_to_oklab_numpy(b)
        elif space == "lab":
            diff = rgb_to_cielab_numpy(a) - rgb_to_cielab_numpy(b)
        else:
            diff = a - b
    else:
        diff = a - b
    return np.linalg.norm(diff, axis=-1)


def ciede2000(lab1: np.ndarray, lab2: np.ndarray) -> np.ndarray:
    """CIEDE2000 colour difference (Sharma et al. 2005), vectorized & broadcasting.
    Inputs are CIELAB (...,3); kL=kC=kH=1. Returns dE2000 with the broadcast
    shape of the inputs' leading axes. Used for the FINAL fixed-palette match,
    where it predicts perceived difference ~1.6x better than Euclidean Lab at a
    cost that is negligible at K-centroids x palette volume."""
    lab1 = np.asarray(lab1, dtype=np.float64)
    lab2 = np.asarray(lab2, dtype=np.float64)
    L1, a1, b1 = lab1[..., 0], lab1[..., 1], lab1[..., 2]
    L2, a2, b2 = lab2[..., 0], lab2[..., 1], lab2[..., 2]

    C1 = np.hypot(a1, b1)
    C2 = np.hypot(a2, b2)
    Cbar = 0.5 * (C1 + C2)
    Cbar7 = Cbar ** 7
    G = 0.5 * (1.0 - np.sqrt(Cbar7 / (Cbar7 + 25.0 ** 7)))
    a1p = (1.0 + G) * a1
    a2p = (1.0 + G) * a2
    C1p = np.hypot(a1p, b1)
    C2p = np.hypot(a2p, b2)

    def _hp(b, ap):
        h = np.degrees(np.arctan2(b, ap))
        return np.where(h < 0, h + 360.0, h)

    h1p = _hp(b1, a1p)
    h2p = _hp(b2, a2p)

    dLp = L2 - L1
    dCp = C2p - C1p

    C1pC2p = C1p * C2p
    dhp = h2p - h1p
    dhp = np.where(dhp > 180.0, dhp - 360.0, dhp)
    dhp = np.where(dhp < -180.0, dhp + 360.0, dhp)
    dhp = np.where(C1pC2p == 0.0, 0.0, dhp)
    dHp = 2.0 * np.sqrt(np.maximum(C1pC2p, 0.0)) * np.sin(np.radians(dhp) / 2.0)

    Lbarp = 0.5 * (L1 + L2)
    Cbarp = 0.5 * (C1p + C2p)

    hsum = h1p + h2p
    habs = np.abs(h1p - h2p)
    hbarp = np.where(
        C1pC2p == 0.0, hsum,
        np.where(habs <= 180.0, 0.5 * hsum,
                 np.where(hsum < 360.0, 0.5 * (hsum + 360.0), 0.5 * (hsum - 360.0))),
    )

    T = (1.0
         - 0.17 * np.cos(np.radians(hbarp - 30.0))
         + 0.24 * np.cos(np.radians(2.0 * hbarp))
         + 0.32 * np.cos(np.radians(3.0 * hbarp + 6.0))
         - 0.20 * np.cos(np.radians(4.0 * hbarp - 63.0)))
    dtheta = 30.0 * np.exp(-(((hbarp - 275.0) / 25.0) ** 2))
    Cbarp7 = Cbarp ** 7
    Rc = 2.0 * np.sqrt(Cbarp7 / (Cbarp7 + 25.0 ** 7))
    Sl = 1.0 + (0.015 * (Lbarp - 50.0) ** 2) / np.sqrt(20.0 + (Lbarp - 50.0) ** 2)
    Sc = 1.0 + 0.045 * Cbarp
    Sh = 1.0 + 0.015 * Cbarp * T
    Rt = -np.sin(np.radians(2.0 * dtheta)) * Rc

    tL = dLp / Sl
    tC = dCp / Sc
    tH = dHp / Sh
    return np.sqrt(tL * tL + tC * tC + tH * tH + Rt * tC * tH)


def lightness(color: np.ndarray | list | tuple, *, space: str = "lab", input_space: str = "rgb") -> np.ndarray:
    arr = np.asarray(color, dtype=np.float64)
    space = space.lower()
    input_space = input_space.lower()
    if input_space == "rgb":
        if space == "oklab":
            return rgb_to_oklab_numpy(arr)[..., 0]
        if space == "lab":
            return rgb_to_cielab_numpy(arr)[..., 0]
    if space == "oklab":
        return arr[..., 0]
    return arr[..., 0]


def perceptual_lightness_from_rgb(color: np.ndarray | list | tuple, *, color_space: str = "lab") -> np.ndarray:
    space = "oklab" if str(color_space or "").strip().lower() == "oklab" else "lab"
    return lightness(color, space=space, input_space="rgb")


__all__ = [
    "rgb_to_xyz",
    "xyz_to_rgb",
    "rgb_to_cielab_numpy",
    "cielab_to_rgb_numpy",
    "rgb_to_oklab_numpy",
    "oklab_to_rgb_numpy",
    "perceptual_distance",
    "ciede2000",
    "lightness",
    "perceptual_lightness_from_rgb",
]
