# ADR 0011: Dokument-Kandidat und Status `ABWEICHENDE_ZUORDNUNG`

**Status:** Accepted

## Context

Die bisherige Zuordnung eines eingehenden Nachweises zu einem Mitglied basierte ausschließlich auf dem Absendernamen (`From`-Header, Fuzzy-Match gegen die Mitgliederliste). Diese Annahme – Absender ist immer die im Nachweis behandelte Person – gilt nicht, wenn ein Mitglied Nachweise anderer Personen sammelt und weiterleitet. Der Admin erkennt das bisher erst beim manuellen Öffnen des PDFs; der Task bleibt bis dahin fälschlich der weiterleitenden Person zugeordnet (siehe Issue #38).

ADR 0003 legt fest, dass eine erfolgreiche Zuordnung als implizite Bestätigung der E-Mail-Adresse gilt. Diese Kopplung war unproblematisch, solange Zuordnung und Absender zwingend identisch waren.

## Decision

Die Extraktion (`extract_from_email`) ermittelt zusätzlich zum Absender-Kandidaten einen unabhängigen Dokument-Kandidaten: einen Namen, der ausschließlich im Text der Dokument-Anhänge (PDF/Bild, nicht im Mail-Body) zeilenweise per Fuzzy-Match gegen die Mitgliederliste gefunden wird (`fuzzy_match_member_in_text`, gleicher Schwellwert `MATCH_THRESHOLD`).

Beide Kandidaten werden in `bestimme_zuordnung` zu einer Entscheidung gebündelt – einer einzigen Funktion, die vom IMAP-Poller-Eingang (`process_email`) und von der Re-Analyse-Funktion (`task_reanalyse`) gemeinsam genutzt wird:

| Absender-Match | Dokument-Match | Ergebnis |
|---|---|---|
| nein | nein | `UNKLARE_ZUORDNUNG` |
| ja | nein | `NEU`, Absender-Kandidat |
| nein | ja | `NEU`, Dokument-Kandidat |
| ja | ja, gleiche Person | `NEU` |
| ja | ja, unterschiedliche Personen | `ABWEICHENDE_ZUORDNUNG` (neu) |

Im neuen Status `ABWEICHENDE_ZUORDNUNG` bleibt `mitglied_nr` leer; beide Kandidaten (Personenkennung + Anzeigename) werden in eigenen Spalten gespeichert und im Nachweis-Posteingang als anklickbare Vorschläge angeboten. Der Admin löst den Widerspruch über denselben Mechanismus wie bei `UNKLARE_ZUORDNUNG` auf (Auswahl per Klick oder freies Dropdown → Status wechselt zu `NEU`).

`ABWEICHENDE_ZUORDNUNG` verhält sich wie `UNKLARE_ZUORDNUNG` bezüglich Sichtbarkeit im Posteingang, Löschbarkeit und direkter Abschließbarkeit über "Erledigt".

**Korrektur an ADR 0003:** Die implizite E-Mail-Bestätigung wird nur noch ausgelöst, wenn die zugeordnete Person tatsächlich mit dem Absender übereinstimmt (Fälle "nur Absender-Match" und "beide matchen dieselbe Person"). Im Fall "nur Dokument-Match" (automatische Zuordnung zum weitergeleiteten Nachweis) bleibt die E-Mail-Adresse der zugeordneten Person unangetastet, da nicht sie die Mail versendet hat.

## Consequences

- Weitergeleitete Nachweise werden automatisch der richtigen Person zugeordnet, wenn der Dokument-Name eindeutig ist – kein manueller Aufwand mehr im Normalfall.
- Bei echtem Widerspruch entscheidet weiterhin der Admin; keine automatische Wahl zwischen den Kandidaten.
- ADR 0003 gilt in seiner ursprünglichen Absicht (nur der tatsächliche Absender gilt als bestätigt) weiterhin – die Kopplung wird an der neuen Fallunterscheidung präzisiert, nicht aufgehoben.
- Zusätzliche additive Spalten auf `tasks` (`kandidat_absender_nr/name`, `kandidat_dokument_nr/name`), nur befüllt im Zustand `ABWEICHENDE_ZUORDNUNG`.
- Bestandstasks, die vor diesem Feature bereits (ggf. falsch) als `NEU` zugeordnet wurden, werden nicht rückwirkend korrigiert.
