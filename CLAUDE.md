# CLAUDE.md – Ablaufende Untersuchungen

Kontext für eine neue Claude Code Instanz, die dieses Projekt weiterentwickelt.

---

## Was dieses Projekt ist

u-checker ist aus einem CLI-Script gewachsen zu einer vollständigen Nachweis-Verwaltungs-Web-App für die Feuerwehr. Sie liest XLS-Exporte aus **MP-Feuer** (Feuerwehrverwaltungssoftware), verwaltet Fälligkeiten ablaufender Untersuchungen, verifiziert E-Mail-Adressen von Mitgliedern und verarbeitet eingehende Nachweis-Dokumente per IMAP.

**Für Domänenbegriffe (Prüfung, Verifikation, Nachweis, Task, ...) und Architekturentscheidungen ist `CONTEXT.md` + `docs/adr/` die maßgebliche Quelle** – nicht dieser Abschnitt. Diese Datei hier hält nur Meta-Anleitung für Agenten fest, keine sich schnell ändernden Produktdetails.

Web-App-Code liegt unter `web/` (`app.py`, `imap_poller.py`, `scheduler.py`, `pdf_export.py`) und ist der einzige Einstiegspunkt (siehe ADR 0013 – der ursprüngliche CLI-Einstiegspunkt wurde entfernt).

---

## Warum bestimmte Entscheidungen so getroffen wurden

### Nur `OK = Nein` Zeilen prüfen
MP-Feuer-Logik: Wenn eine Untersuchung abgeschlossen wird, legt das System automatisch eine neue Zeile für die nächste fällige Untersuchung an (`OK = Nein`). Zeilen mit `OK = Ja` sind historische Einträge – bereits erledigt, nicht relevant.

### Pro Person + Typ: neuester Eintrag gewinnt
Für jede Kombination aus Person und Prüfungstyp kann es mehrere offene Einträge geben. Nur der mit dem spätesten Datum ist die aktuelle Fälligkeit.

### Datum: `Gültig bis` vor `Datum`
`Gültig bis` ist das explizite Ablaufdatum. Wenn leer, ist `Datum` das Fälligkeitsdatum (MP-Feuer trägt es direkt als Zieldatum ein, nicht als Prüfungsdatum).

### Eine Email pro Person
Alle relevanten Prüfungen einer Person werden in einer Email zusammengefasst. Wenn mindestens eine Prüfung **abgelaufen** ist, kommen die Kommandanten auf CC – auch wenn die Person gleichzeitig noch eine laufende Warnung hat.

### Filter: `bei EI anzeigen = Nein`
Spalte AQ. "EI" = Einsatz. Personen die dort "Nein" haben sind ausgeschieden oder nicht mehr einsatzrelevant – werden komplett übersprungen.

### E-Mail: nur `E-Mail privat`
In MP-Feuer wird nur die private E-Mail-Adresse gepflegt. `E-Mail gesch.` ist leer.

---

## MP-Feuer Export – relevante Spalten

| Index | Spaltenname       | Verwendung |
|-------|-------------------|------------|
| 0     | Kurzbezeich.      | Prüfungstyp (G25, G26, FSK, ...) |
| 1     | Prüfung           | Beschreibung (z.B. "G25-Führerschein-Untersuchung") |
| 4     | Datum             | Fälligkeitsdatum (wenn Gültig bis leer) |
| 6     | Gültig bis        | Explizites Ablaufdatum (bevorzugt) |
| 7     | OK                | "Ja" = erledigt/historisch, "Nein" = offen/relevant |
| 18    | Pers.-Nr.         | Eindeutige Personenkennung |
| 20    | Vorname           | |
| 21    | Nachname          | |
| 33    | E-Mail privat     | Empfänger-Adresse |
| 42    | bei EI anzeigen   | "Nein" = Person ausgeschieden → überspringen |

---

## Konfiguration (.env)

```
SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM
KOMMANDANTEN_CC   # kommagetrennte Adressen für CC bei abgelaufenen Prüfungen
WARN_DAYS         # Warnfrist in Tagen (Standard: 90)
PRUEFUNGSTYPEN    # kommagetrennt (Standard: G25, erweiterbar auf G26, FSK, ...)
```

---

## Aktueller Stand

Zielarchitektur und offene Punkte sind in `PRD-nachweis-app.md` und den ADRs unter `docs/adr/` dokumentiert – dort nachsehen statt hier eine Momentaufnahme zu pflegen, die veraltet.

---

## Agent skills

### Issue tracker

Issues leben in GitHub Issues (`github.com/pipe3/u-checker`); externe PRs sind keine Triage-Quelle. Siehe `docs/agents/issue-tracker.md`.

### Triage labels

Standard-Labels: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. Siehe `docs/agents/triage-labels.md`.

### Domain docs

Single-context: eine `CONTEXT.md` + `docs/adr/` im Repo-Root. Siehe `docs/agents/domain.md`.
