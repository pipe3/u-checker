"""Erzeugt ein zusammengeführtes PDF aus einer rohen Email (Body + Anhänge)."""
from __future__ import annotations

import email as email_lib
import io
from email.message import Message
from typing import List, Optional


def email_to_pdf(raw_email: bytes) -> bytes:
    """
    Wandelt eine rohe Email in ein einzelnes PDF um.

    Seite 1: Metadaten + Plaintext-Body (via reportlab)
    Folgeseiten: PDF-Anhänge (direkt eingefügt)
                 Bild-Anhänge (via Pillow in PDF konvertiert)
    """
    msg = email_lib.message_from_bytes(raw_email)

    cover_pdf = _build_cover_pdf(msg)
    attachment_pdfs = _collect_attachment_pdfs(msg)

    if not attachment_pdfs:
        return cover_pdf

    return _merge_pdfs([cover_pdf] + attachment_pdfs)


def _build_cover_pdf(msg: Message) -> bytes:
    """Erstellt eine Titelseite mit Metadaten und Body-Text."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm,
                             topMargin=20 * mm, bottomMargin=20 * mm)
    styles = getSampleStyleSheet()
    meta_style = ParagraphStyle("meta", parent=styles["Normal"], fontSize=9, leading=13, spaceAfter=3)
    body_style = ParagraphStyle("body", parent=styles["Normal"], fontSize=10, leading=15)

    story = []

    story.append(Paragraph("<b>Nachweis-Email</b>", styles["Heading1"]))
    story.append(Spacer(1, 4 * mm))

    for header, label in [("From", "Von"), ("To", "An"), ("Date", "Datum"), ("Subject", "Betreff")]:
        val = msg.get(header, "–")
        story.append(Paragraph(f"<b>{label}:</b> {_escape(val)}", meta_style))

    story.append(Spacer(1, 6 * mm))

    body = _extract_body_text(msg)
    if body:
        story.append(Paragraph("<b>Inhalt:</b>", meta_style))
        story.append(Spacer(1, 2 * mm))
        for line in body.splitlines():
            stripped = line.strip()
            if stripped:
                story.append(Paragraph(_escape(stripped), body_style))
            else:
                story.append(Spacer(1, 3 * mm))

    doc.build(story)
    return buf.getvalue()


def _extract_body_text(msg: Message) -> str:
    """Extrahiert den Plaintext-Body (bevorzugt) oder HTML als Fallback-Text."""
    plain_parts: List[str] = []
    html_parts: List[str] = []

    for part in msg.walk():
        ct = part.get_content_type()
        disp = part.get_content_disposition()
        if disp == "attachment":
            continue
        payload = part.get_payload(decode=True)
        if not payload:
            continue
        if ct == "text/plain":
            plain_parts.append(payload.decode("utf-8", errors="replace"))
        elif ct == "text/html":
            html_parts.append(_html_to_text(payload.decode("utf-8", errors="replace")))

    if plain_parts:
        return "\n".join(plain_parts)
    if html_parts:
        return "\n".join(html_parts)
    return ""


def _html_to_text(html: str) -> str:
    """Einfache HTML-zu-Text-Konvertierung ohne externe Bibliotheken."""
    import re
    text = re.sub(r"<br\s*/?>", "\n", html, flags=re.IGNORECASE)
    text = re.sub(r"</p>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    import html as html_mod
    return html_mod.unescape(text)


def _collect_attachment_pdfs(msg: Message) -> List[bytes]:
    """Sammelt PDF- und Bild-Anhänge als PDF-Bytes."""
    pdfs: List[bytes] = []
    for part in msg.walk():
        if part.get_content_disposition() != "attachment":
            continue
        payload = part.get_payload(decode=True)
        if not payload:
            continue
        ct = part.get_content_type()
        filename = (part.get_filename() or "").lower()

        if ct == "application/pdf" or filename.endswith(".pdf"):
            pdfs.append(payload)
        elif ct.startswith("image/") or any(
            filename.endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".tiff", ".bmp", ".gif")
        ):
            converted = _image_to_pdf(payload)
            if converted:
                pdfs.append(converted)

    return pdfs


def _image_to_pdf(img_bytes: bytes) -> Optional[bytes]:
    """Konvertiert ein Bild in ein einseitiges PDF (A4-Einpassung)."""
    try:
        from PIL import Image
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import mm

        img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        page_w, page_h = A4
        margin = 15 * mm
        max_w = page_w - 2 * margin
        max_h = page_h - 2 * margin

        ratio = min(max_w / img.width, max_h / img.height)
        new_w = img.width * ratio
        new_h = img.height * ratio

        buf = io.BytesIO()
        from reportlab.pdfgen.canvas import Canvas
        c = Canvas(buf, pagesize=A4)
        x = (page_w - new_w) / 2
        y = (page_h - new_h) / 2

        img_buf = io.BytesIO()
        img.save(img_buf, format="JPEG")
        img_buf.seek(0)
        c.drawImage(img_buf, x, y, width=new_w, height=new_h)
        c.save()
        return buf.getvalue()
    except Exception:
        return None


def _merge_pdfs(pdf_list: List[bytes]) -> bytes:
    """Fügt mehrere PDF-Byte-Sequenzen zu einem einzigen PDF zusammen."""
    from pypdf import PdfWriter

    writer = PdfWriter()
    for pdf_bytes in pdf_list:
        from pypdf import PdfReader
        try:
            reader = PdfReader(io.BytesIO(pdf_bytes))
            for page in reader.pages:
                writer.add_page(page)
        except Exception:
            continue

    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _escape(text: str) -> str:
    """XML-Sonderzeichen für reportlab Paragraphs escapen."""
    return (text
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;"))
