# ADR 0003: Nachweis-Eingang gilt als implizite E-Mail-Bestätigung

**Status:** Accepted

## Context
Das System empfängt Nachweise (Untersuchungsdokumente) per IMAP und verknüpft sie mit Mitgliedern. Parallel dazu gibt es eine separate Verifikationsfunktion, die prüft ob E-Mail-Adressen noch aktiv sind.

Wenn ein Mitglied einen Nachweis einschickt, ist damit implizit bewiesen, dass die E-Mail-Adresse aktiv ist und von der Person genutzt wird – ein stärkeres Signal als eine reine Antwort auf eine Verifikationsmail.

## Decision
Wird ein eingehender Nachweis erfolgreich einem Mitglied zugeordnet, wird das Bestätigungsdatum der E-Mail-Verifikation dieses Mitglieds aktualisiert (auf den Zeitpunkt des Nachweis-Eingangs). Eine separate Verifikationsmail an diese Person ist damit hinfällig.

## Consequences
- Mitglieder, die regelmäßig Nachweise einschicken, brauchen keine explizite Verifikation.
- Die Verifikationsliste zeigt realistischere Daten: "zuletzt aktiv" spiegelt echte Kommunikation wider, nicht nur Ping-Antworten.
- Kopplung zwischen Nachweis-Funktion und Verifikations-Funktion: eine Änderung am Zuordnungsverhalten des IMAP-Pollers hat Auswirkungen auf den Verifikationsstatus.
