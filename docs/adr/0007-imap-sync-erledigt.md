# ADR 0007: IMAP-Synchronisierung nur bei Statuswechsel, nicht beim Polling

**Status:** Accepted

## Context

Eingehende Nachweis-Emails werden vom Poller aus der INBOX gelesen und als Tasks in der DB gespeichert (Raw-Email als BLOB). Es war zu entscheiden, wann und wie die Email im IMAP-Postfach bewegt wird:

- Option A: Sofort beim Polling in einen `Nachweise/Offen`-Ordner verschieben
- Option B: Email bleibt in INBOX bis zum Statuswechsel

## Decision

Emails bleiben beim Polling in der INBOX (nur `\Seen` gesetzt). Verschoben wird erst bei Statuswechsel:

- Task → `ERLEDIGT`: Email wird in den IMAP-Ordner `Nachweise` verschoben
- Task wird wiedereröffnet: Email wird zurück in INBOX verschoben
- Task wird als Spam gelöscht: Email wird aus IMAP gelöscht

Die IMAP-UID wird beim Polling pro Task gespeichert. Alle IMAP-Operationen sind best-effort (Fehler werden geloggt, blockieren aber die DB-Aktion nicht). Die DB ist die autoritative Quelle.

## Consequences

- INBOX zeigt immer noch offene Nachweise – das entspricht dem natürlichen E-Mail-Verhalten und ist konsistent mit dem Verifikationsordner-Muster.
- Kein temporärer Zwischenordner nötig; ein einziger `Nachweise`-Ordner reicht.
- IMAP-Ausfall während eines Statuswechsels führt zu Inkonsistenz zwischen DB und IMAP; diese wird nicht automatisch korrigiert.
