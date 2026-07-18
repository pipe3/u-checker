# ADR 0006: Spam wird hart gelöscht, nicht archiviert

**Status:** Accepted

## Context

Im IMAP-Postfach landen neben echten Nachweisen auch Spam-Mails und irrelevante Nachrichten, die der Poller als potenzielle Tasks aufnimmt. Es war zu entscheiden, ob solche Fehlaufnahmen archiviert (Status `IGNORIERT`) oder direkt gelöscht werden.

## Decision

Spam-Tasks werden hart gelöscht: der Datenbankeintrag wird entfernt, die Email wird aus dem IMAP-Postfach gelöscht. Es gibt keinen Archiveintrag. Das Archiv enthält ausschließlich echte, bearbeitete Nachweise.

## Consequences

- Das Archiv bleibt inhaltlich sauber und aussagekräftig – jeder Eintrag ist ein echter Nachweis.
- Versehentlich gelöschte echte Nachweise sind nicht wiederherstellbar. Ein Bestätigungsklick im UI schützt vor unbeabsichtigtem Löschen.
- Kein separater `SPAM`-Status nötig; die Task-Status-Menge bleibt klein.
