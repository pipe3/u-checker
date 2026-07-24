# ADR 0014: RotatingFileHandler + Log-Seite in der Web-App

**Status:** Accepted

## Context

Bei der Diagnose des IMAP-Namespace-Bugs (Sent-Ordner-Fix-Session) mussten Tracebacks per `docker exec`/manuellem Copy-Paste aus dem Container geholt werden, um Fehler wie `_send_admin_notification` SMTPServerDisconnected zu sehen (Issue #44). Die App loggt bisher nur über `logging.getLogger(__name__)`-Aufrufe in `app.py`, `scheduler.py`, `imap_poller.py` und `extractor.py`, ohne eigene `logging`-Konfiguration – faktisch landet das beim Python-/Werkzeug-Default auf stderr, sichtbar nur via `docker logs`. Es existiert keine Logdatei im `/data`-Volume.

Die App hat aktuell keinen eigenen Auth-Layer (kein `flask_login`, kein Passwortschutz) und keinen Reverse-Proxy im Repo – das Sicherheitsmodell verlässt sich auf Netzwerk-/VPN-Schutz, nicht auf Applikationsebene. Logs können personenbezogene Daten wie E-Mail-Adressen enthalten (z.B. `Verifikationsmail an %s fehlgeschlagen`).

Alternativen wurden erwogen:
- **In-Memory Ring-Buffer** statt Datei: verworfen, da Verlust bei jedem Neustart und – relevant für die anstehende Gunicorn-Migration – jeder Worker-Prozess nur seine eigenen Log-Einträge sähe.
- **Level-Filter/Query-Parameter in der UI**: verworfen zugunsten von Einfachheit; bei Bedarf kann im Browser nach `ERROR`/`WARNING` gesucht werden.
- **Log-Einträge gruppieren** (Tracebacks zusammenhalten statt Zeilen-Tail): verworfen zugunsten eines simplen `tail`-Verhaltens über rohe Textzeilen; ein langer Traceback kann dadurch selten am oberen Rand abgeschnitten erscheinen.
- **Eigener Auth-Schutz nur für `/logs`**: verworfen zugunsten von Konsistenz mit dem Rest der App.

## Decision

Ein `RotatingFileHandler` wird zusätzlich zum bestehenden stderr-Output auf den Root-Logger gelegt und schreibt nach `/data/app.log` (`maxBytes=5_000_000`, `backupCount=3`, insgesamt max. ~20 MB). Erfasstes Level: `INFO` (nicht nur `WARNING`), damit auch reguläre Betriebsmeldungen wie "IMAP-Polling: 3 neue Nachweise verarbeitet" sichtbar sind, nicht nur Fehler. Format: `Zeitstempel LEVEL logger.name: Message` (einzeilig, Tracebacks hängen mehrzeilig darunter wie von Python vorgegeben). `docker logs` bleibt als Fallback-Kanal unverändert nutzbar.

Eine neue Route `/logs` bekommt einen eigenen Hauptnav-Eintrag (gleichrangig neben "Einstellungen"). Sie zeigt die letzten 200 Textzeilen aus `app.log` roh, neueste zuerst, ohne Level-Filter. Kein Auto-Refresh – ein manueller "Aktualisieren"-Button lädt neu.

`/logs` bekommt keinen zusätzlichen Zugriffsschutz – dieselbe (fehlende) Absicherung wie jede andere Route der App.

## Consequences

- Diagnose von Fehlern (z.B. SMTP-/IMAP-Abbrüche) ist direkt in der Web-UI möglich, ohne Docker-Host-Zugriff.
- Die Logdatei überlebt Neustarts/Deploys (liegt im persistenten `/data`-Volume) und funktioniert unverändert bei einer künftigen Umstellung auf mehrere Gunicorn-Worker, da alle Worker in dieselbe Datei schreiben.
- Wer `/logs` erreichen kann (jeder mit Netzwerkzugriff auf die App), sieht auch personenbezogene Daten in Fehlermeldungen (E-Mail-Adressen). Das ist eine bewusste Fortschreibung des bestehenden Sicherheitsmodells, kein neues Risiko – sollte aber bei einer künftigen Einführung von Netzwerk-Exposition (z.B. Port-Freigabe ohne VPN) erneut bewertet werden.
- Ein einzelner sehr langer Traceback kann in der 200-Zeilen-Ansicht am oberen Rand abgeschnitten erscheinen, da nach rohen Zeilen und nicht nach logischen Log-Einträgen geschnitten wird.
- Sollte künftig eine Umstellung auf mehrere Gunicorn-Worker-Prozesse erfolgen, muss der `RotatingFileHandler` gegen gleichzeitige Schreibzugriffe mehrerer Prozesse geprüft werden (Python-`logging`-Rotation ist nicht prozessübergreifend sicher) – aktuell kein Problem, da die App einzeln läuft.
