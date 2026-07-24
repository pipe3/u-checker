---
name: verify
description: Projekt-Rezept, um u-checker (Flask-Web-App) laufen zu lassen und live zu testen.
---

# u-checker verifizieren

## Wichtig: lokales Python vs. Produktions-Python

Das lokal installierte `python`/Anaconda-Python auf dieser Maschine ist **3.8**.
Das Produktions-Image (`Dockerfile`) nutzt **Python 3.11**. Das ist relevant für
`imaplib.IMAP4_SSL(..., timeout=...)` – der `timeout`-Parameter existiert erst ab
Python 3.9. Mit lokalem 3.8 schlägt das mit `TypeError: unexpected keyword argument
'timeout'` fehl, obwohl der Code für die Zielumgebung korrekt ist.

**Für runtime-Verifikation (nicht Unit-Tests) daher immer per Docker mit dem
echten Image testen**, nicht mit dem lokalen Anaconda-Python:

```bash
docker build -q -t u-checker-verify .
docker run -d --name u-checker-verify -p 5097:5000 -e SECRET_KEY=verify-secret u-checker-verify
curl -s http://127.0.0.1:5097/
# ... testen ...
docker rm -f u-checker-verify && docker rmi u-checker-verify
```

Settings (inkl. IMAP-Konfiguration) per POST setzen, z.B.:

```bash
curl -s -X POST http://127.0.0.1:5097/settings \
  -d "smtp_host=&smtp_port=&smtp_user=&smtp_from=&kommandanten_cc=&zusammenfassung_an=&warn_days=90&pruefungstypen=G25&imap_host=HOST&imap_port=PORT&imap_user=test@example.com&imap_password=x&imap_poll_minuten=5&imap_nachweis_ordner=&imap_sent_ordner=&imap_verifikation_ordner=" \
  -c cookies.txt -b cookies.txt
```

Danach `POST /imap-poll` bzw. `POST /settings/imap-poll` mit denselben Cookies
aufrufen und die Flash-Message per `GET /` bzw. `GET /settings` prüfen
(`grep "flash error\|flash success"`).

## Timeout-Verhalten testen

- Sofortiger Fehler: `imap_host=127.0.0.1`, `imap_port=1` (Connection refused).
- Echter Hänge-Fall / 30s-Timeout: eine nicht routbare IP wie `10.255.255.1`
  als `imap_host` nutzen (Pakete werden verschluckt, kein RST) – der Request
  kommt nach ~30s mit `TimeoutError: timed out` zurück statt unbegrenzt zu hängen.

## Unit-Tests (für CI, nicht Runtime-Verifikation)

```bash
python -m pytest -q   # mit dem lokalen Anaconda-Python 3.8, Flask ist dort installiert
```
