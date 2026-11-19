"""Pure form builders for domestic imaging write operations.

Each helper validates the facade arguments and returns the complete
business form (including ``deviceSerial``) ready for the wire. Region
gating is performed by the caller in the facade; these helpers do no IO,
no mutation, and no retry.
"""

from .settings import _check_text
from .video import _int_range

_IMAGE_STYLES = ("standard", "soft", "gorgeous", "manual")
_WB_MODES = ("auto", "lock", "manual")


def image_params_form(
    device_serial: str,
    *,
    image_style: str,
    gamma_correction: int,
    gain: int,
    brightness: int | None = None,
    contrast: int | None = None,
    saturation: int | None = None,
    sharpness: int | None = None,
) -> dict[str, str | int]:
    """Build the PUT form for ``/api/v3/device/video/image/params``.

    ``imageStyle`` must be one of standard/soft/gorgeous/manual
    (case-sensitive). In ``manual`` all four detail fields are required;
    in other modes each explicitly supplied field is forwarded
    individually. The wire key is always ``saturation``.
    """
    if not isinstance(image_style, str):
        raise ValueError("imageStyle must be a string")
    if image_style not in _IMAGE_STYLES:
        raise ValueError("imageStyle must be one of standard/soft/gorgeous/manual")

    form: dict[str, str | int] = {
        "deviceSerial": _check_text(device_serial, "deviceSerial"),
        "imageStyle": image_style,
        "gammaCorrection": _int_range(gamma_correction, "gammaCorrection", 0, 9),
        "gain": _int_range(gain, "gain", 0, 100),
    }

    optionals = (
        ("brightness", brightness),
        ("contrast", contrast),
        ("saturation", saturation),
        ("sharpness", sharpness),
    )
    if image_style == "manual":
        for name, value in optionals:
            if value is None:
                raise ValueError(
                    "manual imageStyle requires brightness, contrast, saturation and sharpness"
                )
    for name, value in optionals:
        if value is not None:
            form[name] = _int_range(value, name, 0, 100)
    return form


def white_balance_form(
    device_serial: str,
    mode: str,
    *,
    red: int | None = None,
    blue: int | None = None,
) -> dict[str, str | int]:
    """Build the PUT form for ``/api/v3/device/video/white/balance``.

    ``mode`` must be exactly auto/lock/manual. In ``manual`` both ``red``
    and ``blue`` gains are required (0..255); in auto/lock an explicitly
    supplied gain is still forwarded.
    """
    if not isinstance(mode, str):
        raise ValueError("mode must be a string")
    if mode not in _WB_MODES:
        raise ValueError("mode must be one of auto/lock/manual")
    if mode == "manual" and (red is None or blue is None):
        raise ValueError("manual mode requires both red and blue")

    form: dict[str, str | int] = {
        "deviceSerial": _check_text(device_serial, "deviceSerial"),
        "mode": mode,
    }
    if red is not None:
        form["whiteBalanceRed"] = _int_range(red, "whiteBalanceRed", 0, 255)
    if blue is not None:
        form["whiteBalanceBlue"] = _int_range(blue, "whiteBalanceBlue", 0, 255)
    return form
