# ADR 0001: Jede Antwort auf eine Verifikationsmail gilt als Bestätigung

**Status:** Accepted

## Context
Beim Versand einer Verifikationsmail muss definiert werden, was eine erfolgreiche Bestätigung ausmacht. Alternativen wären: Klick auf einen Link, Antwort mit einem spezifischen Schlüsselwort ("JA", "BESTÄTIGEN"), oder eine beliebige Antwort.

Die Zielgruppe sind Feuerwehrmitglieder ohne einheitliches technisches Niveau.

## Decision
Jede eingehende Antwort auf eine Verifikationsmail – unabhängig vom Inhalt – gilt als Bestätigung. Es wird nur geprüft, ob die `In-Reply-To`-Header-ID mit einer gesendeten Verifikationsmail übereinstimmt.

## Consequences
- Einfachste UX für Mitglieder: einfach antworten, kein Link, kein Codewort.
- Geringes Risiko falscher Bestätigung: Eine fremde Person, die die Adresse übernommen hat, müsste aktiv auf eine Feuerwehr-interne Mail antworten – unwahrscheinlich.
- Keine Unterscheidung zwischen "Bestätigung" und "Rückfrage" – beides zählt gleich.
