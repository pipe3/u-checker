# ADR 0010: Flag `adresse_geaendert` wird bei erfolgreicher Bestätigung zurückgesetzt

**Status:** Accepted

## Context

ADR 0002 legt fest, dass eine Adressänderung im XLS-Import den Verifikationsstatus nicht automatisch zurücksetzt, sondern nur das Flag `adresse_geaendert` setzt und dem Admin als Hinweis anzeigt ("Adresse geändert"). ADR 0002 äußert dabei bereits die Erwartung, dass der Admin "aktiv auf das Flag reagieren" muss, indem er eine neue Verifikation anstößt.

In der Umsetzung fehlt jedoch der Gegenpart: Keine der drei Stellen, die eine Bestätigung (`status='bestaetigt'`) setzen, löscht das Flag `adresse_geaendert` wieder. Der Hinweis "Adresse geändert" bleibt damit dauerhaft sichtbar, selbst nachdem die neue Adresse erfolgreich verifiziert wurde – unabhängig vom Bestätigungsweg (automatisch per In-Reply-To, implizit per Nachweis-Eingang, oder manuell per Admin-Button gemäß ADR 0009).

## Decision

Jede der drei bestehenden Stellen, die `status='bestaetigt'` setzen, setzt zusätzlich `adresse_geaendert=0`:

1. `web/imap_poller.py` – automatische Bestätigung per In-Reply-To-Abgleich (ADR 0001)
2. `web/imap_poller.py` – implizite Bestätigung per zugeordnetem Nachweis-Eingang (ADR 0003)
3. `web/app.py` – manuelle Bestätigung durch Admin-Button (ADR 0009)

Der Reset erfolgt einheitlich und ohne Sonderbehandlung:

- **Kein Adressabgleich beim Nachweis-Eingang**: Ein zugeordneter Nachweis setzt das Flag zurück, unabhängig davon, von welcher Absenderadresse er eingegangen ist. Das ist konsistent mit dem bestehenden Verhalten von ADR 0003, das den Verifikationsstatus ebenfalls ohne Adressabgleich auf `bestaetigt` setzt.
- **Manuelle Bestätigung zählt gleichwertig**: Der "Manuell bestätigen"-Button (ADR 0009) setzt das Flag ebenso zurück wie die automatischen Wege. Der Admin trägt bei dieser Aktion ohnehin bereits die Verantwortung für die Einschätzung "Adresse ist aktiv" – das schließt eine zwischenzeitlich geänderte Adresse ein.
- **Kein Reset bei erneuter Änderung ohne Bestätigung**: Ändert sich die Adresse mehrfach, bevor eine Bestätigung erfolgt, bleibt das Flag einfach `1` (unverändertes Verhalten von ADR 0002). Es wird keine Historie der Zwischenänderungen geführt.
- **Kein Sonderfall für `nie_geprueft` / `ungueltige_adresse`**: Für Mitglieder in diesen Status gibt es keinen Bestätigungsweg, solange keine Verifikation angestoßen wurde. Der Hinweis "Adresse geändert" bleibt dort bewusst bestehen, bis die erste Verifikation erfolgreich abgeschlossen ist – das ist kein Bug, sondern folgt derselben Regel wie alle anderen Fälle.

## Consequences

- Der Hinweis "Adresse geändert" verschwindet, sobald die neue Adresse nachweislich (auf einem der drei anerkannten Wege) bestätigt wurde – das eigentliche Ziel des Flags aus ADR 0002 wird damit erst vollständig erfüllt.
- Keine neuen Zustände oder UI-Elemente nötig; reiner Bugfix an drei bestehenden UPDATE-Stellen.
- Der Nachweis-Eingang (ADR 0003) kann das Flag zurücksetzen, ohne dass die neue Adresse tatsächlich geprüft wurde (kein Adressabgleich) – bewusst in Kauf genommen, analog zur bestehenden Toleranz in ADR 0003 selbst.
