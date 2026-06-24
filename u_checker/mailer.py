import os
import smtplib
from datetime import date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import List

from u_checker.checker import Person

SMTP_HOST = os.getenv("SMTP_HOST", "localhost")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM = os.getenv("SMTP_FROM", "")
KOMMANDANTEN_CC = [e.strip() for e in os.getenv("KOMMANDANTEN_CC", "").split(",") if e.strip()]
ZUSAMMENFASSUNG_AN = [e.strip() for e in os.getenv("ZUSAMMENFASSUNG_AN", "").split(",") if e.strip()]

TEMPLATE_PATH = Path(__file__).parent / "templates" / "email.txt"
ZUSAMMENFASSUNG_TEMPLATE_PATH = Path(__file__).parent / "templates" / "zusammenfassung.txt"


def _load_template() -> str:
    return TEMPLATE_PATH.read_text(encoding="utf-8")


def _format_pruefung(p) -> str:
    datum_str = p.datum.strftime("%d.%m.%Y")
    status_str = "[ABGELAUFEN]" if p.status == "abgelaufen" else "[WARNUNG]"
    return f"  - {p.beschreibung}: fällig am {datum_str} {status_str}"


def _build_message(person: Person, template: str) -> dict:
    pruefungen_liste = "\n".join(_format_pruefung(p) for p in person.pruefungen)
    body = template.format(
        vorname=person.vorname,
        nachname=person.nachname,
        pruefungen_liste=pruefungen_liste,
    )
    return {
        "to": person.email,
        "cc": KOMMANDANTEN_CC if person.hat_abgelaufene else [],
        "subject": "Handlungsbedarf: Ablaufende Untersuchungen",
        "body": body,
    }


def _send(msg: dict):
    mime = MIMEMultipart()
    mime["From"] = SMTP_FROM
    mime["To"] = msg["to"]
    mime["Subject"] = msg["subject"]
    if msg["cc"]:
        mime["Cc"] = ", ".join(msg["cc"])
    mime.attach(MIMEText(msg["body"], "plain", "utf-8"))

    alle_empfaenger = [msg["to"]] + msg["cc"]

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        if SMTP_USER and SMTP_PASSWORD:
            server.login(SMTP_USER, SMTP_PASSWORD)
        server.sendmail(SMTP_FROM, alle_empfaenger, mime.as_string())


def _build_zusammenfassung(persons: List[Person]) -> dict:
    template = ZUSAMMENFASSUNG_TEMPLATE_PATH.read_text(encoding="utf-8")
    heute = date.today()

    eintraege = []
    for person in persons:
        for p in person.pruefungen:
            eintraege.append((person, p))
    eintraege.sort(key=lambda x: x[1].datum)

    abgelaufen = [(p, pr) for p, pr in eintraege if pr.status == "abgelaufen"]
    warnungen = [(p, pr) for p, pr in eintraege if pr.status == "warnung"]

    zeilen = []
    if abgelaufen:
        zeilen.append("ABGELAUFEN:")
        for person, pr in abgelaufen:
            zeilen.append(f"  - {person.nachname}, {person.vorname}: {pr.beschreibung} – {pr.datum.strftime('%d.%m.%Y')}")
        zeilen.append("")

    if warnungen:
        zeilen.append("WARNUNG:")
        for person, pr in warnungen:
            tage = (pr.datum - heute).days
            zeilen.append(f"  - {person.nachname}, {person.vorname}: {pr.beschreibung} – {pr.datum.strftime('%d.%m.%Y')} (in {tage} Tagen)")

    body = template.format(
        datum=heute.strftime("%d.%m.%Y"),
        zusammenfassung="\n".join(zeilen),
        anzahl_personen=len(persons),
        anzahl_abgelaufen=len(abgelaufen),
        anzahl_warnung=len(warnungen),
    )
    return {
        "to": ZUSAMMENFASSUNG_AN,
        "subject": f"Übersicht ablaufende Untersuchungen – {heute.strftime('%d.%m.%Y')}",
        "body": body,
    }


def send_summary(persons: List[Person], dry_run: bool = False):
    if not ZUSAMMENFASSUNG_AN:
        return

    msg = _build_zusammenfassung(persons)

    if dry_run:
        print("\n" + "=" * 60)
        print(f"ZUSAMMENFASSUNG AN: {', '.join(msg['to'])}")
        print(f"BETREFF: {msg['subject']}")
        print("-" * 60)
        print(msg["body"])
    else:
        mime = MIMEMultipart()
        mime["From"] = SMTP_FROM
        mime["To"] = ", ".join(msg["to"])
        mime["Subject"] = msg["subject"]
        mime.attach(MIMEText(msg["body"], "plain", "utf-8"))

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            if SMTP_USER and SMTP_PASSWORD:
                server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(SMTP_FROM, msg["to"], mime.as_string())
        print(f"Zusammenfassung gesendet an {', '.join(msg['to'])}")


def send_notifications(persons: List[Person], dry_run: bool = False):
    if not persons:
        print("Keine Personen mit Handlungsbedarf gefunden.")
        return

    template = _load_template()

    for person in persons:
        msg = _build_message(person, template)

        if dry_run:
            print("\n" + "=" * 60)
            print(f"AN:      {msg['to']}")
            if msg["cc"]:
                print(f"CC:      {', '.join(msg['cc'])}")
            print(f"BETREFF: {msg['subject']}")
            print("-" * 60)
            print(msg["body"])
        else:
            _send(msg)
            cc_info = f" (CC: {', '.join(msg['cc'])})" if msg["cc"] else ""
            print(f"Gesendet an {msg['to']}{cc_info}")
