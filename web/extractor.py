"""Regelbasierte Extraktion von Prüfungstyp, Datum und Mitglied aus Emails."""
from __future__ import annotations

import email.utils
import io
import logging
import re
from datetime import date
from typing import Optional

logger = logging.getLogger(__name__)

MATCH_THRESHOLD = 0.70


def extract_text_from_pdf(pdf_bytes: bytes) -> str:
    # Versuch 1: pdfminer (funktioniert nur bei PDFs mit Textlayer)
    try:
        from pdfminer.high_level import extract_text
        text = extract_text(io.BytesIO(pdf_bytes)) or ""
        if text.strip():
            return text
    except Exception:
        logger.warning("pdfminer-Extraktion fehlgeschlagen", exc_info=True)

    # Versuch 2: PDF → Bild → OCR (für gescannte PDFs ohne Textlayer)
    try:
        from pdf2image import convert_from_bytes
        import pytesseract
        pages = convert_from_bytes(pdf_bytes, dpi=200)
        parts = []
        for page in pages:
            parts.append(_ocr_with_best_rotation(page))
        return "\n".join(p for p in parts if p.strip())
    except Exception:
        logger.warning("PDF-OCR-Fallback fehlgeschlagen", exc_info=True)

    return ""


def _ocr_with_best_rotation(img) -> str:
    """Versucht 0° und 180°, gibt den Text mit höherer Tesseract-Konfidenz zurück."""
    import pytesseract
    best_text = ""
    best_conf = -1.0
    for angle in (0, 180):
        candidate = img.rotate(angle, expand=True) if angle else img
        try:
            data = pytesseract.image_to_data(candidate, lang="deu", output_type=pytesseract.Output.DICT)
            confs = [c for c in data["conf"] if isinstance(c, (int, float)) and c >= 0]
            avg_conf = sum(confs) / len(confs) if confs else 0.0
            if avg_conf > best_conf:
                best_conf = avg_conf
                best_text = " ".join(w for w in data["text"] if w.strip())
        except Exception:
            pass
    return best_text


def extract_text_from_image(img_bytes: bytes) -> str:
    try:
        import pytesseract
        from PIL import Image
        img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        return _ocr_with_best_rotation(img)
    except Exception:
        logger.warning("Bild-OCR fehlgeschlagen", exc_info=True)
        return ""


def _iter_dokument_parts(msg):
    """Liefert alle PDF- und Bild-Teile einer Email als (content_type, filename, payload)-Tupel."""
    for part in msg.walk():
        ct = part.get_content_type()
        filename = part.get_filename() or ""
        fname_lower = filename.lower()
        if ct == "application/pdf" or fname_lower.endswith(".pdf"):
            payload = part.get_payload(decode=True)
            if payload:
                yield ct if ct == "application/pdf" else "application/pdf", filename, payload
        elif ct.startswith("image/") or any(
            fname_lower.endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".tiff", ".bmp")
        ):
            payload = part.get_payload(decode=True)
            if payload:
                yield ct, filename, payload


def collect_text_from_email(msg) -> str:
    """Sammelt Text aus Body und Anhängen (PDF + Bild) einer Email."""
    parts: list[str] = []
    for part in msg.walk():
        ct = part.get_content_type()
        disp = part.get_content_disposition()
        if ct == "text/plain" and disp != "attachment":
            payload = part.get_payload(decode=True)
            if payload:
                parts.append(payload.decode("utf-8", errors="replace"))
    for ct, _filename, payload in _iter_dokument_parts(msg):
        if ct == "application/pdf":
            parts.append(extract_text_from_pdf(payload))
        else:
            parts.append(extract_text_from_image(payload))
    return "\n".join(p for p in parts if p)


def parse_pruefungstyp(text: str, valid_types: list[str]) -> Optional[str]:
    """Sucht den ersten bekannten Prüfungstyp (Wortgrenze) im Text.

    Berücksichtigt typische OCR-Fehllesarten: G→6 (z.B. G26→626).
    """
    def patterns_for(typ: str) -> list[str]:
        p = [re.escape(typ)]
        # G25 → 625, G26 → 626 etc.
        if re.match(r"^G\d", typ, re.IGNORECASE):
            p.append(re.escape("6" + typ[1:]))
        return p

    for typ in valid_types:
        for pat in patterns_for(typ):
            if re.search(r"\b" + pat + r"\b", text, re.IGNORECASE):
                return typ.upper()
    return None


def parse_datum(text: str) -> Optional[date]:
    """
    Sucht das Fälligkeitsdatum im deutschen Format (d.m.yyyy oder dd.mm.yyyy).

    Priorität:
    1. Datum nach Fälligkeits-Keywords (gültig bis, nächste Untersuchung, …)
    2. Alle Daten ohne Geburtstags-Kontext, bevorzugt innerhalb plausiblem Bereich
    """
    _DATE_PATTERN = r"(\d{1,2})\.(\d{1,2})\.(\d{4})"

    # Stufe 1: Datum nach Fälligkeits-Keywords
    fälligkeit_pattern = (
        r"(?:gültig\s+bis|gueltig\s+bis|ablaufdatum|gültigkeit|validity|valid\s+until"
        r"|nächste\s+untersuchung|naechste\s+untersuchung|nachuntersuchung"
        r"|nächste\s+vorsorge|wiedervorlage|fällig\s+am|fällig\s+bis)"
        r"[\s:–\-]*" + _DATE_PATTERN
    )
    m = re.search(fälligkeit_pattern, text, re.IGNORECASE)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            pass

    # Stufe 2: Positionen aller Datumsangaben ermitteln, Geburtstags-Kontext ausschließen
    geburtstag_pattern = re.compile(
        r"(?:geb(?:oren|\.)|geburtsdatum|birthdate|date\s+of\s+birth)"
        r"[\s:]*" + _DATE_PATTERN,
        re.IGNORECASE,
    )
    geburtstag_spans = {m.start() for m in geburtstag_pattern.finditer(text)}

    heute = date.today()
    kandidaten: list[date] = []
    for m in re.finditer(r"\b" + _DATE_PATTERN + r"\b", text):
        if m.start() in geburtstag_spans:
            continue
        try:
            d = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            continue
        # Geburtsdaten herausfiltern: mehr als 15 Jahre in der Vergangenheit
        if (heute - d).days > 15 * 365:
            continue
        kandidaten.append(d)

    if kandidaten:
        # Frühestes Datum bevorzugen (nächste Fälligkeit)
        return min(kandidaten)
    return None


def load_members_from_xls(xls_path: str) -> list[dict]:
    """Lädt aktive Mitglieder aus dem MP-Feuer XLS-Export."""
    try:
        import xlrd
        wb = xlrd.open_workbook(xls_path)
        sh = wb.sheets()[0]
        seen: set[str] = set()
        members: list[dict] = []
        for r in range(1, sh.nrows):
            row = sh.row_values(r)
            pers_nr = str(row[18]).strip()
            if not pers_nr or pers_nr in seen:
                continue
            if str(row[42]).strip() == "Nein":
                continue
            seen.add(pers_nr)
            members.append({
                "pers_nr": pers_nr,
                "vorname": str(row[20]).strip(),
                "nachname": str(row[21]).strip(),
                "email": str(row[33]).strip(),
            })
        return members
    except Exception:
        return []


def fuzzy_match_member(name: str, members: list[dict]) -> tuple[Optional[dict], float]:
    """Fuzzy-Match eines Namens gegen die Mitgliederliste. Gibt (Mitglied, Score 0-1) zurück."""
    if not name or not members:
        return None, 0.0
    try:
        from rapidfuzz import fuzz, process
        candidates = {m["pers_nr"]: f"{m['vorname']} {m['nachname']}" for m in members}
        result = process.extractOne(name, candidates, scorer=fuzz.token_sort_ratio)
        if result is None:
            return None, 0.0
        _matched_val, score, matched_key = result
        matched = next((m for m in members if m["pers_nr"] == matched_key), None)
        return matched, score / 100.0
    except Exception:
        logger.warning("fuzzy_match_member fehlgeschlagen – rapidfuzz verfügbar?", exc_info=True)
        return None, 0.0


def extract_from_email(msg, valid_types: list[str], members: list[dict]) -> dict:
    """
    Extrahiert Prüfungstyp, Datum und Mitglied aus einer Email (inkl. Anhänge).

    Rückgabe: {pruefungstyp, faelligkeitsdatum, mitglied, match_score, raw_text}
    """
    raw_text = collect_text_from_email(msg)

    pruefungstyp = parse_pruefungstyp(raw_text, valid_types)
    faelligkeitsdatum = parse_datum(raw_text)

    # Name aus From-Header als Basis für das Fuzzy-Matching
    from_raw = msg.get("From", "")
    sender_name, _ = email.utils.parseaddr(from_raw)

    matched_member, score = fuzzy_match_member(sender_name, members)

    return {
        "pruefungstyp": pruefungstyp,
        "faelligkeitsdatum": faelligkeitsdatum,
        "mitglied": matched_member,
        "match_score": score,
        "raw_text": raw_text,
    }
