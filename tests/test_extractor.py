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
    bestimme_zuordnung,
    collect_text_from_dokumente,
    collect_text_from_email,
    extract_from_email,
    fuzzy_match_member,
    fuzzy_match_member_in_text,
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


# ---------- fuzzy_match_member_in_text ----------

def test_fuzzy_match_in_text_findet_zeile():
    text = "Untersuchungsbescheinigung\nErika Musterfrau\nG25 gültig bis 31.12.2026"
    member, score = fuzzy_match_member_in_text(text, MEMBERS)
    assert member is not None
    assert member["pers_nr"] == "002"
    assert score >= MATCH_THRESHOLD


def test_fuzzy_match_in_text_ignoriert_leere_zeilen():
    text = "\n\nMax Mustermann\n\n"
    member, score = fuzzy_match_member_in_text(text, MEMBERS)
    assert member is not None
    assert member["pers_nr"] == "001"


def test_fuzzy_match_in_text_kein_treffer():
    text = "Nichts Erkennbares\nAuch nicht hier"
    member, score = fuzzy_match_member_in_text(text, MEMBERS)
    assert score < MATCH_THRESHOLD


def test_fuzzy_match_in_text_leerer_text():
    member, score = fuzzy_match_member_in_text("", MEMBERS)
    assert member is None
    assert score == 0.0


# ---------- collect_text_from_dokumente (Body-Ausschluss) ----------

def test_collect_text_aus_dokumenten_ignoriert_body():
    msg = _pdf_email("Max Mustermann <max@example.com>")
    with patch("web.extractor.extract_text_from_pdf", return_value="Erika Musterfrau"):
        text = collect_text_from_dokumente(msg)
    assert "Anbei mein Nachweis" not in text
    assert "Erika Musterfrau" in text


def test_collect_text_aus_dokumenten_ohne_anhang_ist_leer():
    msg = _text_email("Max <max@x.com>", "Nur Body-Text, kein Anhang.")
    assert collect_text_from_dokumente(msg) == ""


# ---------- extract_from_email: Dokument-Kandidat ----------

def test_extraktion_dokument_kandidat_aus_pdf():
    """Absender matched niemanden, PDF-Anhang nennt eindeutig ein Mitglied."""
    msg = _pdf_email("Weiterleiter <weiterleiter@example.com>")
    with patch("web.extractor.extract_text_from_pdf", return_value="Erika Musterfrau\nG25 gültig bis 31.12.2026"):
        result = extract_from_email(msg, VALID_TYPES, MEMBERS)

    assert result["match_score"] < MATCH_THRESHOLD
    assert result["dokument_mitglied"] is not None
    assert result["dokument_mitglied"]["pers_nr"] == "002"
    assert result["dokument_match_score"] >= MATCH_THRESHOLD


def test_extraktion_dokument_kandidat_ignoriert_signatur_im_body():
    """Body enthält Signatur des Absenders – darf den Dokument-Kandidaten nicht beeinflussen."""
    msg = email.mime.multipart.MIMEMultipart()
    msg["From"] = "Max Mustermann <max@example.com>"
    msg["Subject"] = "Nachweis"
    msg["Message-ID"] = "<sig@x.com>"
    msg.attach(email.mime.text.MIMEText("Anbei der Nachweis.\n\nMit freundlichen Grüßen\nMax Mustermann"))
    att = email.mime.application.MIMEApplication(b"%PDF-1.4 fake", _subtype="pdf")
    att.add_header("Content-Disposition", "attachment", filename="nachweis.pdf")
    msg.attach(att)

    with patch("web.extractor.extract_text_from_pdf", return_value="Erika Musterfrau\nG25 gültig bis 31.12.2026"):
        result = extract_from_email(msg, VALID_TYPES, MEMBERS)

    assert result["mitglied"]["pers_nr"] == "001"
    assert result["dokument_mitglied"]["pers_nr"] == "002"


# ---------- bestimme_zuordnung ----------

def _extraction(mitglied=None, score=0.0, dokument_mitglied=None, dokument_score=0.0) -> dict:
    return {
        "mitglied": mitglied,
        "match_score": score,
        "dokument_mitglied": dokument_mitglied,
        "dokument_match_score": dokument_score,
    }


def test_zuordnung_weder_absender_noch_dokument_match():
    extraction = _extraction(score=0.2, dokument_score=0.1)
    result = bestimme_zuordnung(extraction, MEMBERS)
    assert result["status"] == "UNKLARE_ZUORDNUNG"
    assert result["mitglied_nr"] is None


def test_zuordnung_nur_absender_match():
    extraction = _extraction(mitglied=MEMBERS[0], score=0.95, dokument_score=0.1)
    result = bestimme_zuordnung(extraction, MEMBERS)
    assert result["status"] == "NEU"
    assert result["mitglied_nr"] == "001"
    assert result["sender_bestaetigt"] is True


def test_zuordnung_nur_dokument_match():
    extraction = _extraction(score=0.1, dokument_mitglied=MEMBERS[1], dokument_score=0.95)
    result = bestimme_zuordnung(extraction, MEMBERS)
    assert result["status"] == "NEU"
    assert result["mitglied_nr"] == "002"
    assert result["sender_bestaetigt"] is False


def test_zuordnung_beide_match_gleiche_person():
    extraction = _extraction(mitglied=MEMBERS[0], score=0.95, dokument_mitglied=MEMBERS[0], dokument_score=0.9)
    result = bestimme_zuordnung(extraction, MEMBERS)
    assert result["status"] == "NEU"
    assert result["mitglied_nr"] == "001"
    assert result["sender_bestaetigt"] is True


def test_zuordnung_beide_match_unterschiedliche_personen():
    extraction = _extraction(mitglied=MEMBERS[0], score=0.95, dokument_mitglied=MEMBERS[1], dokument_score=0.9)
    result = bestimme_zuordnung(extraction, MEMBERS)
    assert result["status"] == "ABWEICHENDE_ZUORDNUNG"
    assert result["mitglied_nr"] is None
    assert result["kandidat_absender_nr"] == "001"
    assert result["kandidat_absender_name"] == "Max Mustermann"
    assert result["kandidat_dokument_nr"] == "002"
    assert result["kandidat_dokument_name"] == "Erika Musterfrau"
    assert result["sender_bestaetigt"] is False


def test_zuordnung_ohne_mitgliederliste():
    extraction = _extraction(score=0.0, dokument_score=0.0)
    result = bestimme_zuordnung(extraction, [])
    assert result["status"] == "NEU"
    assert result["mitglied_nr"] is None
