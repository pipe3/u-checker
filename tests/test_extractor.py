"""Tests für web/extractor.py – regelbasierte Extraktion (Issue #7)."""
import email.message
import email.mime.application
import email.mime.image
import email.mime.multipart
import email.mime.text
from datetime import date
from unittest.mock import patch

import pytest

from web.extractor import (
    MATCH_THRESHOLD,
    collect_text_from_email,
    extract_from_email,
    fuzzy_match_member,
    parse_datum,
    parse_pruefungstyp,
)

MEMBERS = [
    {"pers_nr": "001", "vorname": "Max", "nachname": "Mustermann", "email": "max@example.com"},
    {"pers_nr": "002", "vorname": "Erika", "nachname": "Musterfrau", "email": "erika@example.com"},
]

VALID_TYPES = ["G25", "G26", "FSK"]


def _text_email(from_addr: str, body: str, mid: str = "<t@x.com>") -> email.message.EmailMessage:
    msg = email.message.EmailMessage()
    msg["From"] = from_addr
    msg["Subject"] = "Nachweis"
    msg["Message-ID"] = mid
    msg.set_content(body)
    return msg


def _pdf_email(from_addr: str, mid: str = "<t@x.com>"):
    msg = email.mime.multipart.MIMEMultipart()
    msg["From"] = from_addr
    msg["Subject"] = "Nachweis"
    msg["Message-ID"] = mid
    msg.attach(email.mime.text.MIMEText("Anbei mein Nachweis."))
    att = email.mime.application.MIMEApplication(b"%PDF-1.4 fake", _subtype="pdf")
    att.add_header("Content-Disposition", "attachment", filename="nachweis.pdf")
    msg.attach(att)
    return msg


def _image_email(from_addr: str, mid: str = "<t@x.com>"):
    msg = email.mime.multipart.MIMEMultipart()
    msg["From"] = from_addr
    msg["Subject"] = "Nachweis"
    msg["Message-ID"] = mid
    msg.attach(email.mime.text.MIMEText("Anbei mein Nachweis."))
    att = email.mime.image.MIMEImage(b"fakeimgdata", _subtype="jpeg")
    att.add_header("Content-Disposition", "attachment", filename="nachweis.jpg")
    msg.attach(att)
    return msg


# ---------- parse_pruefungstyp ----------

def test_parse_pruefungstyp_gefunden():
    assert parse_pruefungstyp("Mein G25 Nachweis", VALID_TYPES) == "G25"


def test_parse_pruefungstyp_case_insensitive():
    assert parse_pruefungstyp("ergebnis: g25", VALID_TYPES) == "G25"


def test_parse_pruefungstyp_nicht_gefunden():
    assert parse_pruefungstyp("Allgemeines Schreiben", VALID_TYPES) is None


def test_parse_pruefungstyp_kein_partial_match():
    # "IG25" darf nicht als "G25" erkannt werden
    assert parse_pruefungstyp("IG25 Blah", VALID_TYPES) is None


def test_parse_pruefungstyp_zweiter_typ():
    assert parse_pruefungstyp("Ergebnis der G26-Untersuchung", VALID_TYPES) == "G26"


# ---------- parse_datum ----------

def test_parse_datum_standard():
    assert parse_datum("Gültig bis: 31.12.2026") == date(2026, 12, 31)


def test_parse_datum_einstellige_teile():
    assert parse_datum("Datum: 1.3.2027") == date(2027, 3, 1)


def test_parse_datum_nicht_gefunden():
    assert parse_datum("Kein Datum im Text") is None


def test_parse_datum_ungueltig():
    assert parse_datum("Datum: 99.99.2026") is None


def test_parse_datum_bevorzugt_anker_vor_weiterleitungsdatum():
    # Weiterleitungsheader: 25.06.2026; Ablaufdatum im Inhalt: 31.12.2027
    text = "Am 25.06.2026 schrieb Max:\nGültig bis: 31.12.2027\n"
    assert parse_datum(text) == date(2027, 12, 31)


def test_parse_datum_fallback_ohne_anker():
    # Kein Keyword → erstes Datum im Text
    assert parse_datum("Untersuchung 15.03.2025 abgeschlossen") == date(2025, 3, 15)


# ---------- fuzzy_match_member ----------

def test_fuzzy_match_exakt():
    member, score = fuzzy_match_member("Max Mustermann", MEMBERS)
    assert member is not None
    assert member["pers_nr"] == "001"
    assert score >= MATCH_THRESHOLD


def test_fuzzy_match_umgekehrte_reihenfolge():
    member, score = fuzzy_match_member("Mustermann Max", MEMBERS)
    assert member is not None
    assert member["pers_nr"] == "001"
    assert score >= MATCH_THRESHOLD


def test_fuzzy_match_kein_treffer():
    member, score = fuzzy_match_member("Xyzzy Niemand", MEMBERS)
    assert score < MATCH_THRESHOLD


def test_fuzzy_match_leere_liste():
    member, score = fuzzy_match_member("Max Mustermann", [])
    assert member is None
    assert score == 0.0


def test_fuzzy_match_leerer_name():
    member, score = fuzzy_match_member("", MEMBERS)
    assert member is None
    assert score == 0.0


def test_fuzzy_match_zweites_mitglied():
    member, score = fuzzy_match_member("Erika Musterfrau", MEMBERS)
    assert member is not None
    assert member["pers_nr"] == "002"
    assert score >= MATCH_THRESHOLD


# ---------- collect_text_from_email ----------

def test_collect_text_aus_body():
    msg = _text_email("Max <max@x.com>", "G25 Nachweis gültig bis 31.12.2026")
    text = collect_text_from_email(msg)
    assert "G25" in text
    assert "31.12.2026" in text


def test_collect_text_aus_pdf_anhang():
    msg = _pdf_email("Max <max@x.com>")
    with patch("web.extractor.extract_text_from_pdf", return_value="G25 Gültig bis 31.12.2026"):
        text = collect_text_from_email(msg)
    assert "G25" in text


def test_collect_text_aus_bild_anhang():
    msg = _image_email("Max <max@x.com>")
    with patch("web.extractor.extract_text_from_image", return_value="G25 Gültig bis 31.12.2026"):
        text = collect_text_from_email(msg)
    assert "G25" in text


# ---------- extract_from_email ----------

def test_extraktion_pdf_anhang():
    msg = _pdf_email("Max Mustermann <max@example.com>")
    pdf_text = "G25 Nachweis\nGültig bis: 31.12.2026"
    with patch("web.extractor.extract_text_from_pdf", return_value=pdf_text):
        result = extract_from_email(msg, VALID_TYPES, MEMBERS)

    assert result["pruefungstyp"] == "G25"
    assert result["faelligkeitsdatum"] == date(2026, 12, 31)
    assert result["mitglied"] is not None
    assert result["mitglied"]["pers_nr"] == "001"
    assert result["match_score"] >= MATCH_THRESHOLD


def test_extraktion_bild_anhang():
    msg = _image_email("Max Mustermann <max@example.com>")
    ocr_text = "G25 Nachweis\nGültig bis: 31.12.2026"
    with patch("web.extractor.extract_text_from_image", return_value=ocr_text):
        result = extract_from_email(msg, VALID_TYPES, MEMBERS)

    assert result["pruefungstyp"] == "G25"
    assert result["faelligkeitsdatum"] == date(2026, 12, 31)


def test_extraktion_nur_text():
    msg = _text_email(
        "Max Mustermann <max@example.com>",
        "G25 Nachweis\nGültig bis: 31.12.2026",
    )
    result = extract_from_email(msg, VALID_TYPES, MEMBERS)

    assert result["pruefungstyp"] == "G25"
    assert result["faelligkeitsdatum"] == date(2026, 12, 31)
    assert result["mitglied"] is not None
    assert result["mitglied"]["pers_nr"] == "001"


def test_extraktion_fehlender_name():
    msg = _text_email(
        "unknown@example.com",
        "G25 Nachweis\nGültig bis: 31.12.2026",
    )
    result = extract_from_email(msg, VALID_TYPES, MEMBERS)

    assert result["pruefungstyp"] == "G25"
    assert result["match_score"] < MATCH_THRESHOLD


def test_extraktion_fehlender_typ():
    msg = _text_email(
        "Max Mustermann <max@example.com>",
        "Allgemeines Schreiben\nGültig bis: 31.12.2026",
    )
    result = extract_from_email(msg, VALID_TYPES, MEMBERS)

    assert result["pruefungstyp"] is None
    assert result["faelligkeitsdatum"] == date(2026, 12, 31)


def test_extraktion_fehlendes_datum():
    msg = _text_email(
        "Max Mustermann <max@example.com>",
        "G25 Nachweis ohne Datum",
    )
    result = extract_from_email(msg, VALID_TYPES, MEMBERS)

    assert result["pruefungstyp"] == "G25"
    assert result["faelligkeitsdatum"] is None


def test_extraktion_ohne_mitgliederliste():
    msg = _text_email(
        "Max Mustermann <max@example.com>",
        "G25 Nachweis\nGültig bis: 31.12.2026",
    )
    result = extract_from_email(msg, VALID_TYPES, [])

    assert result["pruefungstyp"] == "G25"
    assert result["mitglied"] is None
    assert result["match_score"] == 0.0
