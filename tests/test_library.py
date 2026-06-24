import pytest
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

from u_checker import check_examinations, send_notifications, send_summary
from u_checker.checker import Person, Pruefung


# --- Import-API ---

def test_check_examinations_importierbar():
    assert callable(check_examinations)


def test_send_notifications_importierbar():
    assert callable(send_notifications)


def test_send_summary_importierbar():
    assert callable(send_summary)


# --- Datenmodell ---

def test_person_hat_abgelaufene_true():
    person = Person(
        pers_nr="001",
        vorname="Max",
        nachname="Muster",
        email="max@example.com",
        pruefungen=[
            Pruefung(typ="G25", beschreibung="G25-Test", datum=date.today() - timedelta(days=1), status="abgelaufen"),
        ],
    )
    assert person.hat_abgelaufene


def test_person_hat_abgelaufene_false():
    person = Person(
        pers_nr="001",
        vorname="Max",
        nachname="Muster",
        email="max@example.com",
        pruefungen=[
            Pruefung(typ="G25", beschreibung="G25-Test", datum=date.today() + timedelta(days=30), status="warnung"),
        ],
    )
    assert not person.hat_abgelaufene


def test_person_hat_abgelaufene_gemischt():
    person = Person(
        pers_nr="001",
        vorname="Max",
        nachname="Muster",
        email="max@example.com",
        pruefungen=[
            Pruefung(typ="G25", beschreibung="G25-Test", datum=date.today() + timedelta(days=30), status="warnung"),
            Pruefung(typ="G26", beschreibung="G26-Test", datum=date.today() - timedelta(days=5), status="abgelaufen"),
        ],
    )
    assert person.hat_abgelaufene


# --- send_notifications dry_run ---

def test_send_notifications_leere_liste(capsys):
    send_notifications([], dry_run=True)
    out = capsys.readouterr().out
    assert "Keine Personen" in out


def test_send_notifications_warnung(capsys):
    persons = [
        Person(
            pers_nr="001",
            vorname="Anna",
            nachname="Müller",
            email="anna@example.com",
            pruefungen=[
                Pruefung(typ="G25", beschreibung="G25-Test", datum=date.today() + timedelta(days=30), status="warnung"),
            ],
        )
    ]
    send_notifications(persons, dry_run=True)
    out = capsys.readouterr().out
    assert "anna@example.com" in out
    assert "G25-Test" in out
    assert "CC" not in out


def test_send_notifications_abgelaufen_mit_cc(capsys, monkeypatch):
    monkeypatch.setattr("u_checker.mailer.KOMMANDANTEN_CC", ["kommandant@example.com"])
    persons = [
        Person(
            pers_nr="002",
            vorname="Karl",
            nachname="Brand",
            email="karl@example.com",
            pruefungen=[
                Pruefung(typ="G25", beschreibung="G25-Test", datum=date.today() - timedelta(days=10), status="abgelaufen"),
            ],
        )
    ]
    send_notifications(persons, dry_run=True)
    out = capsys.readouterr().out
    assert "karl@example.com" in out
    assert "kommandant@example.com" in out


# --- check_examinations mit Mock ---

@patch("u_checker.checker.xlrd")
def test_check_examinations_nur_header(mock_xlrd):
    mock_wb = MagicMock()
    mock_sh = MagicMock()
    mock_sh.nrows = 1
    mock_wb.sheets.return_value = [mock_sh]
    mock_xlrd.open_workbook.return_value = mock_wb

    result = check_examinations("dummy.xls")
    assert result == []


@patch("u_checker.checker.xlrd")
def test_check_examinations_ok_ja_wird_uebersprungen(mock_xlrd):
    heute = date.today()
    mock_wb = MagicMock()
    mock_sh = MagicMock()
    mock_sh.nrows = 2
    row = ["G25", "G25-Test", "", "", 0, "", 0, "Ja"] + [""] * 35
    mock_sh.row_values.return_value = row
    mock_wb.sheets.return_value = [mock_sh]
    mock_xlrd.open_workbook.return_value = mock_wb

    result = check_examinations("dummy.xls")
    assert result == []


@patch("u_checker.checker.xlrd")
def test_check_examinations_ei_nein_wird_uebersprungen(mock_xlrd):
    """Zeile mit 'bei EI anzeigen = Nein' wird übersprungen, auch wenn Datum vorhanden."""
    abgelaufen = date.today() - timedelta(days=5)
    mock_wb = MagicMock()
    mock_sh = MagicMock()
    mock_sh.nrows = 2
    row = [""] * 43
    row[0] = "G25"
    row[1] = "G25-Test"
    row[4] = 44927.0   # echtes Datum via Mock
    row[6] = 0
    row[7] = "Nein"    # OK = Nein → relevant
    row[18] = "001"
    row[20] = "Max"
    row[21] = "Muster"
    row[33] = "max@example.com"
    row[42] = "Nein"   # bei EI anzeigen = Nein → ausscheiden
    mock_sh.row_values.return_value = row
    mock_wb.sheets.return_value = [mock_sh]
    mock_wb.datemode = 0
    mock_xlrd.open_workbook.return_value = mock_wb
    mock_xlrd.xldate_as_tuple.return_value = (abgelaufen.year, abgelaufen.month, abgelaufen.day, 0, 0, 0)

    result = check_examinations("dummy.xls")
    assert result == []


@patch("u_checker.checker.xlrd")
def test_check_examinations_abgelaufene_pruefung(mock_xlrd):
    heute = date.today()
    abgelaufen = heute - timedelta(days=5)

    mock_wb = MagicMock()
    mock_sh = MagicMock()
    mock_sh.nrows = 2

    row = [""] * 43
    row[0] = "G25"
    row[1] = "G25-Untersuchung"
    row[4] = 44927.0  # wird via _xl_to_date gemocked
    row[6] = 0        # kein Gültig-bis
    row[7] = "Nein"
    row[18] = "001"
    row[20] = "Anna"
    row[21] = "Meier"
    row[33] = "anna@example.com"
    row[42] = "Ja"

    mock_sh.row_values.return_value = row
    mock_wb.sheets.return_value = [mock_sh]
    mock_wb.datemode = 0
    mock_xlrd.open_workbook.return_value = mock_wb
    mock_xlrd.xldate_as_tuple.return_value = (abgelaufen.year, abgelaufen.month, abgelaufen.day, 0, 0, 0)

    result = check_examinations("dummy.xls")
    assert len(result) == 1
    assert result[0].email == "anna@example.com"
    assert result[0].pruefungen[0].status == "abgelaufen"
