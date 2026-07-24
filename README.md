# Ablaufende Untersuchungen

u-checker liest XLS-Exporte aus **MP-Feuer** (Feuerwehrverwaltungssoftware), verwaltet Fälligkeiten ablaufender Untersuchungen, verifiziert E-Mail-Adressen von Mitgliedern und verarbeitet eingehende Nachweis-Dokumente per IMAP – als Web-App mit Dashboard.

Für Domänenbegriffe (Prüfung, Verifikation, Nachweis, Task, ...) und den genauen Ablauf siehe [`CONTEXT.md`](CONTEXT.md) sowie die Architekturentscheidungen unter [`docs/adr/`](docs/adr/).

## Setup

```bash
cp .env.example .env
# .env mit eigenen Zugangsdaten befüllen
```

## Betrieb (Docker)

```bash
docker build -t u-checker .
docker run -d \
  --name u-checker \
  -p 5000:5000 \
  --env-file .env \
  -v "$(pwd)/data:/data" \
  u-checker
```

Die Web-App ist danach unter `http://localhost:5000` erreichbar. Das Volume unter `/data` hält die persistenten Daten (Mitglieder-Datenbank, Verifikationsstatus etc.) über Container-Neustarts hinweg.

## Konfiguration (.env)

| Variable | Beschreibung |
|---|---|
| `SMTP_HOST` | SMTP-Server |
| `SMTP_PORT` | SMTP-Port (Standard: 587) |
| `SMTP_USER` | Benutzername |
| `SMTP_PASSWORD` | Passwort |
| `SMTP_FROM` | Absender-Adresse |
| `KOMMANDANTEN_CC` | CC-Adressen bei abgelaufenen Untersuchungen (kommagetrennt) |
| `ZUSAMMENFASSUNG_AN` | Empfänger der Gesamtübersicht aller Fälligkeiten (kommagetrennt) |
| `WARN_DAYS` | Warnfrist in Tagen (Standard: 90) |
| `PRUEFUNGSTYPEN` | Zu prüfende Typen aus MP-Feuer (kommagetrennt, Standard: G25) |
| `IMAP_HOST` | IMAP-Server für den Nachweis-Abruf |
| `IMAP_PORT` | IMAP-Port (Standard: 993) |
| `IMAP_USER` | IMAP-Benutzername |
| `IMAP_PASSWORD` | IMAP-Passwort |
| `IMAP_POLL_MINUTEN` | Abrufintervall des Postfachs in Minuten (Standard: 5) |

## E-Mail-Template

Das Template liegt in `templates/email.txt` und kann frei bearbeitet werden.
Verfügbare Platzhalter: `{vorname}`, `{nachname}`, `{pruefungen_liste}`
