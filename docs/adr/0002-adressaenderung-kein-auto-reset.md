# ADR 0002: Adressänderung im XLS-Import setzt Verifikationsstatus nicht automatisch zurück

**Status:** Accepted

## Context
Wenn ein neuer XLS-Import eine andere E-Mail-Adresse für ein bekanntes Mitglied enthält, muss entschieden werden, was mit dem bestehenden Verifikationsstatus passiert. Die neue Adresse wurde noch nicht bestätigt, aber der Admin könnte die Änderung absichtlich oder versehentlich importiert haben (z.B. Test-Export, falsches File).

## Decision
Der Verifikationsstatus bleibt erhalten. Stattdessen wird ein Flag `adresse_geaendert` gesetzt und die neue Adresse gespeichert. Der Admin sieht den Hinweis in der Verifikationsliste und entscheidet selbst, ob eine neue Verifikation angestoßen werden soll.

## Consequences
- Kein Datenverlust bei versehentlichem Import einer falschen XLS-Datei.
- Admin behält volle Kontrolle – kein impliziter Versand durch einen Import-Vorgang.
- Admin muss aktiv auf das Flag reagieren; ein übersehenes Flag bedeutet, dass eine unbestätigte neue Adresse weiter als "bestätigt" angezeigt werden könnte, bis die Verifikation manuell neu gestartet wird.
