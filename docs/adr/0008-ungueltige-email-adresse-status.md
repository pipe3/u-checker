# ADR 0008: Formatvalidierung von E-Mail-Adressen mit eigenem Verifikationsstatus

**Status:** Accepted

## Context

Eine E-Mail-Adresse im XLS-Import war fehlerhaft eingetragen ("vorname-name at domain.de" statt mit "@"). Der Verifikationsmail-Versand schlug daraufhin nicht sichtbar fehl: `smtplib` validiert das Adressformat nicht clientseitig, sodass `send_verifikationsmail` klaglos durchlief und der Verifikationsstatus auf `ausstehend` gesetzt wurde – ohne dass Admin oder System den Fehler bemerkten.

Zwei unabhängige Verteidigungslinien wären denkbar: Formatvalidierung vor dem Versand, oder Bounce-Erkennung nach dem Versand (z.B. über den IMAP-Poller). Letzteres würde auch Adressen abfangen, die formal gültig aber unzustellbar sind, erfordert aber deutlich mehr Aufbau (DSN-Erkennung, eigene Fehlerklasse). Für den konkreten Fehlerfall – ein fehlendes "@" – reicht eine einfache Formatprüfung aus.

Für die Modellierung war zusätzlich zu entscheiden, ob eine ungültige Adresse ein eigener Verifikationsstatus wird oder nur ein zusätzliches Flag neben dem bestehenden Status (analog zu `adresse_geaendert`).

## Decision

Die Formatvalidierung (Minimalcheck: genau ein `@`, Text davor/danach, Domain-Teil mit Punkt) läuft ausschließlich vor dem Versand, nicht als Bounce-Erkennung danach. Sie greift an zwei Stellen:

1. Beim XLS-Import (`_sync_email_verifikation`), bei jedem Sync-Durchlauf für jede Zeile – nicht nur bei erkannter Adressänderung, da sonst unveränderte Alt-Einträge mit bereits ungültiger Adresse nie geprüft würden.
2. Als zweite Verteidigungslinie direkt vor dem tatsächlichen Versand (`email_pruefung_senden`), falls Bestandsdaten den Import-Check umgangen haben.

Ungültige Adressen bekommen einen eigenen vierten Verifikationsstatus `ungueltige_adresse` statt eines zusätzlichen Flags. Wird die Adresse per Import auf ein gültiges Format korrigiert, springt der Status automatisch auf `nie_geprueft` zurück.

Explizit nicht im Scope: Bounce-Erkennung nach dem Versand, sowie der separate Pfad für Fälligkeits-Erinnerungen (`Person.email` / `send_notifications`) – dort gilt dieselbe Lücke weiterhin und müsste in einem eigenständigen Fix behandelt werden.

## Consequences

- Der gemeldete Fehlerfall (fehlendes "@") wird zuverlässig und früh erkannt, sichtbar in der Verifikationsliste statt stillschweigend als `ausstehend` markiert.
- Als eigener Status ist `ungueltige_adresse` filterbar und zählt in die Dashboard-Kachel für Handlungsbedarf mit ein – klarer sichtbar als ein Flag neben einem irreführenden `nie_geprueft`/`ausstehend`-Status.
- Formal gültige, aber unzustellbare Adressen (z.B. Tippfehler in einer existierenden Domain) werden weiterhin nicht erkannt – das erfordert Bounce-Erkennung, die bewusst zurückgestellt wurde.
- Die Fälligkeits-Erinnerungen (zweiter Versandpfad) bleiben von diesem Fix unberührt und tragen dieselbe Schwäche unverändert weiter.
