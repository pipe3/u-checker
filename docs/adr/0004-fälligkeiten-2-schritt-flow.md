# ADR 0004: 2-Schritt-Flow für Fälligkeiten (Analyse → Auswahl → Senden)

**Status:** Accepted

## Context

Bisher löste ein einzelner Klick ("Jetzt ausführen") sofort E-Mails aus, mit einem optionalen Dry-Run-Checkbox als Sicherheitsnetz. Der Benutzer wollte mehr Kontrolle: erst sehen, wer welche E-Mail bekommt, dann gezielt auswählen, dann senden – analog zum bestehenden Auswahlmuster auf der Email-Prüfung-Seite.

## Decision

Der Fälligkeiten-Workflow wird in zwei explizite Schritte aufgeteilt:

1. **Analyse:** Liest die XLS-Datei und zeigt eine Vorschau (Name, Prüfungstypen+Status, frühestes Datum, CC-Flag). Kein Versand.
2. **Auswahl + Senden:** Der Benutzer wählt aktiv Mitglieder aus (keine Vorauswahl, "Alle auswählen"-Button verfügbar) und löst den Versand explizit aus.

Der Dry-Run-Checkbox entfällt vollständig – die Analyse ist der neue Dry-Run. Beide Schritte leben auf einer einzigen Seite (`/faelligkeiten`). Zwischen Analyse und Senden wird kein State gespeichert: beim Senden wird die Analyse intern wiederholt und auf die ausgewählten Mitglieder gefiltert.

## Consequences

- Kein versehentlicher Massen-Versand mehr möglich.
- Der Benutzer muss immer zwei Aktionen ausführen – auch wenn er alle auswählen will.
- Die XLS-Datei wird pro Send-Vorgang zweimal gelesen (einmal für die Vorschau, einmal beim Senden). Akzeptabel, da die Datei sich im manuellen Workflow nicht ändert.
- `ergebnis.html` und der Dry-Run-Pfad werden entfernt.
