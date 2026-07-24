# ADR 0012: Expliziter Timeout für IMAP-Poll-Verbindung

**Status:** Accepted

## Context

`poll_inbox()` (`web/imap_poller.py`) öffnet eine `imaplib.IMAP4_SSL`-Verbindung ohne `timeout`-Parameter. Damit können Verbindungsaufbau und jeder nachfolgende Socket-Read (Login, Select, Search, Fetch) unbegrenzt blockieren, falls der IMAP-Server nicht antwortet.

Beobachtetes Symptom: Der manuelle Poll (über den Button in den Einstellungen) hing wiederholt endlos – der Ladebalken im Browser lief unbegrenzt, es erschien nie eine Flash-Message. Nach einem Neuladen der Seite zeigte sich jedoch, dass die Mails im Hintergrund tatsächlich abgerufen worden waren. Das passt zum Bild eines Requests, der serverseitig in einem blockierenden Socket-Read feststeckt, irgendwann doch durchläuft, dessen Response den Browser aber nie erreicht (Verbindung bereits abgebrochen).

`poll_inbox()` wird sowohl von einer manuell ausgelösten Route als auch periodisch vom Scheduler-Job `_imap_poll_job` (`web/scheduler.py`) aufgerufen – ein Hänger blockiert also potenziell nicht nur einen einzelnen Request, sondern auch den Scheduler-Thread.

`open_sent_connection()` (`web/imap_poller.py`, IMAP-APPEND in den Sent-Ordner beim Mail-Versand) hat dieselbe Schwachstelle, wurde hier aber bewusst nicht angefasst – das ist als eigenständiges Thema ausgeklammert (siehe Sent-Ordner-IMAP-APPEND-Fix, separates Issue).

## Decision

`poll_inbox()` übergibt `timeout=30` (Sekunden) an `imaplib.IMAP4_SSL(...)`. 30 Sekunden wurde als Kompromiss gewählt: großzügig genug für Login/Select/Search/Fetch auch bei mehreren wartenden Mails, aber klar begrenzt gegen unbegrenztes Hängen.

`open_sent_connection()` bleibt vorerst ohne Timeout – die gleiche Behandlung dieser Stelle ist bewusst zurückgestellt, nicht vergessen.

## Consequences

- Ein nicht erreichbarer oder extrem langsamer IMAP-Server führt nach spätestens 30 Sekunden zu einem regulären Fehler (Flash-Message bzw. geloggter Scheduler-Fehler) statt zu einem unbegrenzt hängenden Request oder Thread.
- Der Timeout-Wert ist eine Schätzung, kein gemessener Grenzwert; bei wiederkehrenden falschen Timeouts (z.B. sehr viele oder sehr große Mails im Postfach) muss er ggf. nachjustiert werden.
- `open_sent_connection()` trägt weiterhin dasselbe Risiko unbegrenzten Blockierens – das ist bekannt und für ein künftiges Issue vorgesehen, kein Widerspruch zu dieser Entscheidung.
