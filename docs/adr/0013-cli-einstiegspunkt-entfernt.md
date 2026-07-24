# ADR 0013: CLI-Einstiegspunkt (main.py) entfernt

**Status:** Accepted

## Context

`main.py` war der ursprüngliche CLI-Einstiegspunkt des Projekts (`python main.py export.xls [--dry-run]`), aus dem die Web-App (`web/`) später herausgewachsen ist. CLAUDE.md beschrieb ihn als weiterhin "als Ausgangspunkt fortbestehend".

Bei der Untersuchung eines anderen Bugs fiel auf: `main.py` wird von `web/` nirgendwo importiert oder aufgerufen, der Docker-Container startet ausschließlich `python web/app.py` (Dockerfile `CMD`), und seit dem Initial-Commit wurde `main.py` inhaltlich nicht mehr verändert – nur der `u_checker`-Package-Refactor hat `checker`/`mailer` dorthin verschoben.

Funktional ist `main.py` der Web-App bereits hinterhergefallen: Es ruft zwar dieselben `check_examinations`/`send_notifications`/`send_summary`-Funktionen wie `web/app.py` auf, aber ohne die IMAP-Sent-Ordner-Nachbildung (ADR-Kontext Issue #45) und ohne die Task-Antwort-Verarbeitung – beides nur in `web/app.py` verdrahtet. Ein CLI-Aufruf hätte also inkonsistente Mail-Historie erzeugt (kein Sent-Ordner-Abgleich).

Rückfrage beim Projektinhaber ergab: Der manuelle CLI-Aufruf mit einer Excel-Datei wird in der Praxis nicht mehr genutzt, auch nicht als Notfall-Fallback bei einer down Web-App.

## Decision

`main.py` wird ersatzlos entfernt. Es gibt keinen CLI-Einstiegspunkt mehr – die Web-App (`web/app.py`) ist der einzige Weg, einen XLS-Export zu verarbeiten und Benachrichtigungen zu versenden.

Die von `main.py` genutzten Exports in `u_checker/__init__.py` (`send_notifications`, `send_summary`, `check_examinations`) bleiben unverändert bestehen, da `web/app.py` und mehrere Tests (`tests/test_library.py`) sie unabhängig von `main.py` als Package-Interface verwenden.

## Consequences

- Es gibt keinen CLI-Fallback mehr, falls die Web-App down ist. Ein Nachweis-Import ist dann nur nach Wiederherstellung der Web-App möglich.
- `main.py` erzeugte ohnehin keine korrekte Mail-Historie mehr (fehlender Sent-Ordner-Abgleich) – der Rückbau entfernt einen Pfad, der bei Nutzung zu Inkonsistenzen geführt hätte, statt einen funktionierenden Fallback zu opfern.
- CLAUDE.md und README.md müssen von main.py-Referenzen befreit und auf den Docker/Web-App-Betrieb umgestellt werden (separater Umsetzungsschritt).
