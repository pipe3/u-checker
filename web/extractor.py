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
    try:
        from pdfminer.high_level import extract_text
        return extract_text(io.BytesIO(pdf_bytes)) or ""
    except Exception:
        return ""


def extract_text_from_image(img_bytes: bytes) -> str:
    try:
        import pytesseract
        from PIL import Image
        img = Image.open(io.BytesIO(img_bytes))
        return pytesseract.image_to_string(img, lang="deu")
    except Exception:
        return ""


def collect_text_from_email(msg) -> str:
    """Sammelt Text aus Body und Anhängen (PDF + Bild) einer Email."""
    parts: list[str] = []
    for part in msg.walk():
        ct = part.get_content_type()
        disp = part.get_content_disposition()
        filename = (part.get_filename() or "").lower()

        if ct == "text/plain" and disp != "attachment":
            payload = part.get_payload(decode=True)
            if payload:
                parts.append(payload.decode("utf-8", errors="replace"))
        elif ct == "application/pdf" or filename.endswith(".pdf"):
            payload = part.get_payload(decode=True)
            if payload:
                parts.append(extract_text_from_pdf(payload))
        elif ct.startswith("image/") or any(
            filename.endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".tiff", ".bmp")
        ):
            payload = part.get_payload(decode=True)
            if payload:
                parts.append(extract_text_from_image(payload))

    return "\n".join(p for p in parts if p)


def parse_pruefungstyp(text: str, valid_types: list[str]) -> Optional[str]:
    """Sucht den ersten bekannten Prüfungstyp (Wortgrenze) im Text."""
    for typ in valid_types:
        if re.search(r"\b" + re.escape(typ) + r"\b", text, re.IGNORECASE):
            return typ.upper()
    return None


def parse_datum(text: str) -> Optional[date]:
    """
    Sucht ein Datum im deutschen Format (d.m.yyyy oder dd.mm.yyyy).

    Bevorzugt Datumsangaben nach Schlüsselwörtern wie "Gültig bis" oder
    "Ablaufdatum", um Weiterleitungsdaten in Email-Headern zu vermeiden.
    """
    _DATE_PATTERN = r"(\d{1,2})\.(\d{1,2})\.(\d{4})"

    # Erst: Datum nach Ablauf-Schlüsselwörtern suchen
    anchored = re.search(
        r"(?:gültig\s+bis|gueltig\s+bis|ablaufdatum|gültigkeit|validity|valid\s+until)"
        r"[\s:–-]*" + _DATE_PATTERN,
        text,
        re.IGNORECASE,
    )
    if anchored:
        try:
            return date(int(anchored.group(3)), int(anchored.group(2)), int(anchored.group(1)))
        except ValueError:
            pass

    # Fallback: erstes Datum im Text
    m = re.search(r"\b" + _DATE_PATTERN + r"\b", text)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            pass
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
