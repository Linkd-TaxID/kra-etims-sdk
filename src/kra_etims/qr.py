"""
KRA eTIMS SDK — Offline QR Code Generator
==========================================
Takes a signed receipt response from the TIaaS middleware and locally renders
the strict KRA QR string so a POS developer can send it straight to a thermal
printer without a second round-trip to the backend.

The KRA QR payload (eTIMS v2.0) is already embedded in the middleware
response as the ``qrCode`` field — this module's job is two-fold:

1. ``render_kra_qr_string()``  — extract / validate the canonical string.
2. ``generate_qr_bytes()``     — turn that string into a PNG image (bytes)
   that a thermal printer driver can consume directly.

The ``qrcode`` and ``Pillow`` packages are optional.  String generation
always works; image generation raises ``ImportError`` with a clear message
if the packages are absent.  Install with:

    pip install "kra-etims-sdk[qr]"
"""

from __future__ import annotations

from typing import Optional


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def render_kra_qr_string(receipt_response: dict) -> str:
    """
    Extract the canonical KRA QR string from a signed receipt response.

    The TIaaS middleware embeds the fully-signed, VSCU-stamped QR payload
    in the ``qrCode`` key of every successful invoice response.

    Parameters
    ----------
    receipt_response:
        The dict returned by ``client.submit_sale()``.

    Returns
    -------
    str
        The raw KRA QR string, ready to be encoded into a barcode.

    Raises
    ------
    ValueError
        If the response does not contain a ``qrCode`` field (e.g. if the
        invoice is still pending or an error response was passed in).

    Example
    -------
    >>> response = client.submit_sale(invoice)
    >>> qr_string = render_kra_qr_string(response)
    >>> print(qr_string)
    20260311T113000;CU12345678;INV-2026-001;NS;1;4310.34;689.66;0;0;0;0;0;5000.00
    """
    # The middleware response may nest data inside a "data" key.
    data = receipt_response.get("data", receipt_response)

    qr_string = (
        data.get("qrCode")
        or data.get("qr_code")
        or data.get("qrcode")
        or receipt_response.get("qrCode")
        or receipt_response.get("qr_code")
    )

    if not qr_string:
        raise ValueError(
            "No 'qrCode' field found in the receipt response. "
            "Ensure the invoice was submitted successfully and the middleware "
            "returned a signed receipt before calling render_kra_qr_string()."
        )

    return str(qr_string).strip()


def generate_qr_bytes(
    qr_string: str,
    *,
    box_size: int = 10,
    border: int = 4,
    error_correction: str = "M",
) -> bytes:
    """
    Render a KRA QR string as a PNG image and return the raw bytes.

    Suitable for streaming directly to a thermal receipt printer driver or
    embedding in a PDF receipt without writing to disk.

    Parameters
    ----------
    qr_string:
        The canonical KRA QR string (from ``render_kra_qr_string()``).
    box_size:
        Pixel size of each QR module.  10 gives ~290×290 px at standard density.
    border:
        Quiet zone in modules (KRA spec requires ≥4).
    error_correction:
        QR error correction level: "L" (7%), "M" (15%), "Q" (25%), "H" (30%).
        "M" is sufficient for clean thermal prints; use "H" for damaged labels.

    Returns
    -------
    bytes
        PNG image bytes.

    Raises
    ------
    ImportError
        If ``qrcode[pil]`` is not installed.

    Example
    -------
    >>> png_bytes = generate_qr_bytes(render_kra_qr_string(response))
    >>> with open("receipt_qr.png", "wb") as f:
    ...     f.write(png_bytes)
    >>> # Or stream to thermal printer:
    >>> printer.write(png_bytes)
    """
    try:
        import qrcode  # type: ignore[import]
        from qrcode.constants import (  # type: ignore[import]
            ERROR_CORRECT_L,
            ERROR_CORRECT_M,
            ERROR_CORRECT_Q,
            ERROR_CORRECT_H,
        )
        from io import BytesIO
    except ImportError as exc:
        raise ImportError(
            "QR image generation requires the 'qrcode[pil]' package. "
            "Install it with:  pip install 'kra-etims-sdk[qr]'"
        ) from exc

    _ec_map = {
        "L": ERROR_CORRECT_L,
        "M": ERROR_CORRECT_M,
        "Q": ERROR_CORRECT_Q,
        "H": ERROR_CORRECT_H,
    }
    ec_level = _ec_map.get(error_correction.upper(), ERROR_CORRECT_M)

    qr = qrcode.QRCode(
        version=None,          # auto-determine smallest version
        error_correction=ec_level,
        box_size=box_size,
        border=border,
    )
    qr.add_data(qr_string)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def save_qr_image(
    qr_string: str,
    output_path: str,
    **kwargs,
) -> None:
    """
    Convenience wrapper: render and save a QR PNG to ``output_path``.

    Parameters
    ----------
    qr_string:
        The canonical KRA QR string.
    output_path:
        Destination file path (e.g. ``"/tmp/receipt_qr.png"``).
    **kwargs:
        Forwarded to ``generate_qr_bytes()``.
    """
    png_bytes = generate_qr_bytes(qr_string, **kwargs)
    with open(output_path, "wb") as fh:
        fh.write(png_bytes)
