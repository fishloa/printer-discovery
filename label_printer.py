"""
Core label printing logic — shared by CLI and web service.
Renders PDFs, images, and Word docs to ZPL bitmaps scaled for a Zebra ZD621 (4x6 labels).
"""

import io
import json
import os
import socket
import subprocess
import tempfile
from pathlib import Path

from PIL import Image
import fitz  # PyMuPDF

# Zebra ZD621 4x6 label dimensions in inches
LABEL_WIDTH_IN = 4.0
LABEL_HEIGHT_IN = 6.0
DEFAULT_DPI = 300

# Margins in mm: left, top, right, bottom (negative = shift toward edge)
MARGINS_MM = (6.0, -2.0, 0.0, 0.0)


def _mm_to_px(mm: float, dpi: int) -> int:
    return int(mm / 25.4 * dpi)


def get_printer_config() -> dict:
    config_path = Path(__file__).parent / "config.json"
    with open(config_path) as f:
        config = json.load(f)
    return config["printers"][0]


def pdf_bytes_to_image(pdf_bytes: bytes, dpi: int = DEFAULT_DPI) -> Image.Image:
    """Render first page of a PDF (from bytes) to a PIL Image."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    page = doc[0]
    scale = dpi / 72.0
    mat = fitz.Matrix(scale, scale)
    pix = page.get_pixmap(matrix=mat, alpha=False)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    doc.close()
    return img


def pdf_file_to_image(pdf_path: str, dpi: int = DEFAULT_DPI) -> Image.Image:
    """Render first page of a PDF file to a PIL Image."""
    with open(pdf_path, "rb") as f:
        return pdf_bytes_to_image(f.read(), dpi)


def _auto_crop(img: Image.Image) -> Image.Image:
    """Trim whitespace padding from around content, keeping borders intact."""
    gray = img.convert("L")
    # Find bounding box of all non-white pixels (threshold catches near-white)
    bw = gray.point(lambda x: 0 if x > 250 else 255)
    bbox = bw.getbbox()
    if bbox:
        return img.crop(bbox)
    return img


def fit_to_label(img: Image.Image, dpi: int = DEFAULT_DPI, rotate: bool = False) -> Image.Image:
    """Auto-crop whitespace, then scale and pad to fit a 4x6 label."""
    img = _auto_crop(img)

    label_w = int(LABEL_WIDTH_IN * dpi)
    label_h = int(LABEL_HEIGHT_IN * dpi)

    m_left = _mm_to_px(MARGINS_MM[0], dpi)
    m_top = _mm_to_px(MARGINS_MM[1], dpi)
    m_right = _mm_to_px(MARGINS_MM[2], dpi)
    m_bottom = _mm_to_px(MARGINS_MM[3], dpi)
    printable_w = label_w - m_left - m_right
    printable_h = label_h - m_top - m_bottom

    if rotate:
        img = img.rotate(90, expand=True)

    img_ratio = img.width / img.height
    label_ratio = label_w / label_h
    if (img_ratio > 1.0) != (label_ratio > 1.0):
        img = img.rotate(90, expand=True)

    scale = min(printable_w / img.width, printable_h / img.height)
    new_w = int(img.width * scale)
    new_h = int(img.height * scale)
    img = img.resize((new_w, new_h), Image.LANCZOS)

    canvas = Image.new("RGB", (label_w, label_h), "white")
    x_offset = m_left + (printable_w - new_w) // 2
    y_offset = m_top + (printable_h - new_h) // 2
    canvas.paste(img, (x_offset, y_offset))
    return canvas


def image_to_zpl(img: Image.Image) -> bytes:
    """Convert a PIL Image to ZPL ~DG commands."""
    mono = img.convert("1")
    width_px = mono.width
    height_px = mono.height
    bytes_per_row = (width_px + 7) // 8
    total_bytes = bytes_per_row * height_px

    pixels = mono.load()
    rows = []
    for y in range(height_px):
        row_bytes = []
        for bx in range(bytes_per_row):
            byte_val = 0
            for bit in range(8):
                px_x = bx * 8 + bit
                if px_x < width_px:
                    if pixels[px_x, y] == 0:
                        byte_val |= (0x80 >> bit)
            row_bytes.append(byte_val)
        rows.append(bytes(row_bytes))

    bitmap_hex = b"".join(rows).hex().upper()
    zpl = (
        f"^XA\n"
        f"~DGR:LABEL.GRF,{total_bytes},{bytes_per_row},\n"
        f"{bitmap_hex}\n"
        f"^FO0,0^XGR:LABEL.GRF,1,1^FS\n"
        f"^IDR:LABEL.GRF^FS\n"
        f"^XZ\n"
    )
    return zpl.encode("ascii")


def send_to_printer(data: bytes, ip: str, port: int = 9100):
    """Send raw data to the printer over TCP."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(10)
        s.connect((ip, port))
        s.sendall(data)


def file_to_image(file_bytes: bytes, filename: str, dpi: int = DEFAULT_DPI) -> Image.Image:
    """Convert uploaded file (PDF, image, or Word doc) to a PIL Image."""
    ext = Path(filename).suffix.lower()

    if ext == ".pdf":
        return pdf_bytes_to_image(file_bytes, dpi)

    if ext in (".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".tif", ".webp"):
        return Image.open(io.BytesIO(file_bytes)).convert("RGB")

    if ext in (".docx", ".doc"):
        # Convert Word → PDF via LibreOffice, then render
        with tempfile.TemporaryDirectory() as tmpdir:
            doc_path = os.path.join(tmpdir, f"input{ext}")
            with open(doc_path, "wb") as f:
                f.write(file_bytes)
            subprocess.run(
                ["soffice", "--headless", "--convert-to", "pdf", "--outdir", tmpdir, doc_path],
                check=True, capture_output=True, timeout=30,
            )
            pdf_path = os.path.join(tmpdir, "input.pdf")
            with open(pdf_path, "rb") as f:
                return pdf_bytes_to_image(f.read(), dpi)

    raise ValueError(f"Unsupported file type: {ext}")


def process_and_preview(file_bytes: bytes, filename: str, dpi: int = DEFAULT_DPI) -> bytes:
    """File bytes → fitted label preview as PNG bytes."""
    img = file_to_image(file_bytes, filename, dpi)
    img = fit_to_label(img, dpi)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def process_and_print(file_bytes: bytes, filename: str, ip: str = None, dpi: int = DEFAULT_DPI):
    """File bytes → ZPL → send to printer."""
    if not ip:
        ip = get_printer_config()["ip"]
    img = file_to_image(file_bytes, filename, dpi)
    img = fit_to_label(img, dpi)
    zpl = image_to_zpl(img)
    send_to_printer(zpl, ip)
