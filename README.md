# Ablaufende Untersuchungen

Prüft einen MP-Feuer XLS-Export auf ablaufende Untersuchungen und verschickt automatisch Benachrichtigungen per E-Mail.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env
# .env mit eigenen Zugangsdaten befüllen
```

## Verwendung

```bash
# Vorschau: Emails anzeigen ohne zu senden
python main.py export.xls --dry-run

# Emails tatsächlich versenden
python main.py export.xls
```

## Konfiguration (.env)

| Variable | Beschreibung |
|---|---|
| `SMTP_HOST` | SMTP-Server |
| `SMTP_PORT` | SMTP-Port (Standard: 587) |
| `SMTP_USER` | Benutzername |
| `SMTP_PASSWORD` | Passwort |
| `SMTP_FROM` | Absender-Adresse |
| `KOMMANDANTEN_CC` | CC-Adressen bei abgelaufenen Untersuchungen (kommagetrennt) |
| `WARN_DAYS` | Warnfrist in Tagen (Standard: 90) |
| `PRUEFUNGSTYPEN` | Zu prüfende Typen aus MP-Feuer (Standard: G25) |

## E-Mail-Template

Das Template liegt in `templates/email.txt` und kann frei bearbeitet werden.  
Verfügbare Platzhalter: `{vorname}`, `{nachname}`, `{pruefungen_liste}`

## Logik

- Nur Einträge mit `OK = Nein` werden geprüft (offene Untersuchungen)
- Pro Person + Typ wird der neueste Eintrag verwendet
- Datum: `Gültig bis` wenn vorhanden, sonst `Datum`
- Fällig in ≤ WARN_DAYS Tagen → Warnung an Person
- Bereits abgelaufen → Warnung an Person + CC Kommandanten
- Eine E-Mail pro Person mit allen relevanten Untersuchungen
