# Context: u-checker

## Glossary

### Mitglied
Eine aktive Person in der Feuerwehr, die im XLS-Export aus MP-Feuer enthalten ist und das Flag `bei EI anzeigen = Ja` trägt. Ausgeschiedene oder nicht-einsatzrelevante Personen (Flag = `Nein`) sind keine Mitglieder im Sinne dieser App – sie werden vollständig ignoriert. Eindeutig identifiziert durch die `Pers.-Nr.`.

### Prüfung
Eine einzelne Untersuchungsfälligkeit eines Mitglieds für einen bestimmten Prüfungstyp (z.B. G25, G26). Eine Prüfung hat ein Fälligkeitsdatum und einen Status: `warnung` (innerhalb der Warnfrist) oder `abgelaufen`. Historische Einträge (OK = Ja) sind keine Prüfungen – sie existieren in der App nicht.

### Prüfungstyp
Die Kurzbezeichnung einer Untersuchungskategorie (z.B. `G25`, `G26`, `FSK`). Welche Typen aktiv geprüft werden, ist konfigurierbar. Nicht konfigurierte Typen werden beim Import ignoriert.

### Verifikation
Der Prozess, mit dem bestätigt wird, dass eine E-Mail-Adresse eines Mitglieds noch aktiv und erreichbar ist. Eine Verifikation besteht aus dem Versand einer Verifikationsmail und dem Abwarten einer Antwort. Nicht zu verwechseln mit einer inhaltlichen Prüfung der Identität – es wird nur geprüft, ob die Adresse funktioniert.

### Verifikationsstatus
Der aktuelle Zustand der E-Mail-Verifikation eines Mitglieds. Mögliche Zustände:
- `nie_geprueft` – Es wurde noch keine Verifikationsmail gesendet (Ausgangszustand für neue Mitglieder).
- `ausstehend` – Eine Verifikationsmail wurde gesendet, aber noch keine Antwort eingegangen. Enthält das Sendedatum.
- `bestaetigt` – Eine Bestätigung wurde empfangen. Enthält das Bestätigungsdatum sowie die Herkunft der Bestätigung (`automatisch` oder `manuell`).
- `ungueltige_adresse` – Die hinterlegte E-Mail-Adresse erfüllt nicht einmal ein minimales Formatkriterium (z.B. fehlendes `@`) und wurde deshalb gar nicht erst zum Versand einer Verifikationsmail zugelassen. Wird bei jedem XLS-Import geprüft, nicht nur bei Adressänderung. Fällt beim nächsten Import automatisch auf `nie_geprueft` zurück, sobald die Adresse formal wieder gültig ist.
- `re_verifikation_ausstehend` – Ein Mitglied war bereits `bestaetigt`, es wurde jedoch erneut eine Verifikationsmail versendet (z.B. versehentlich oder nach langer Zeit). Enthält weiterhin das alte Bestätigungsdatum zusammen mit dem neuen Sendedatum, damit die frühere Bestätigung für den Admin sichtbar bleibt. Geht eine passende Antwort auf die neue Verifikationsmail ein, springt der Status auf `bestaetigt` mit aktualisiertem Datum. Es gibt keinen automatischen Timeout – bleibt eine Antwort dauerhaft aus, verbleibt der Status unbegrenzt hier, bis der Admin manuell bestätigt.

Zusätzlich kann das Flag `adresse_geaendert` gesetzt sein, wenn der letzte XLS-Import eine abweichende E-Mail-Adresse für dieses Mitglied enthielt.

### Bestätigung
Das Ereignis, das den Verifikationsstatus eines Mitglieds auf `bestaetigt` setzt. Eine Bestätigung entsteht automatisch durch eine direkte Antwort auf eine Verifikationsmail oder durch den Eingang eines Nachweises (Nachweis-Funktion), oder manuell durch einen Admin-Eingriff (siehe Manuelle Bestätigung). Eine automatische Bestätigung ist kein explizites "JA" der Person – jede Antwort auf die Verifikationsmail zählt. Jede Bestätigung trägt eine grobe Herkunft, `automatisch` oder `manuell` – eine feinere Unterscheidung zwischen Antwort und Nachweis wird nicht getroffen.

### Manuelle Bestätigung
Ein Admin-Eingriff, der den Verifikationsstatus eines Mitglieds direkt auf `bestaetigt` setzt, ohne dass ein automatischer Abgleich (In-Reply-To oder Nachweis) stattgefunden hat. Dient Fällen, in denen die Bestätigung nachweislich vorliegt, aber außerhalb der vom System erwarteten Kanäle eintraf – z.B. Antwort per Chat oder an eine persönliche E-Mail-Adresse statt per Reply auf die Verifikationsmail. Nur möglich, wenn bereits eine Verifikationsmail versendet wurde (Status `ausstehend` oder `re_verifikation_ausstehend`); bei `nie_geprueft` oder `ungueltige_adresse` gibt es nichts zu bestätigen. Wird in der Verifikationsliste sichtbar als "manuell bestätigt am X" vermerkt, um die abweichende Herkunft von einer systemvalidierten Bestätigung erkennbar zu halten.

### Verifikationsmail
Eine E-Mail, die an ein Mitglied gesendet wird, um dessen E-Mail-Adresse zu bestätigen. Der Text ist über ein konfigurierbares Template einstellbar. Die `Message-ID` der gesendeten Mail wird gespeichert, um eingehende Antworten via `In-Reply-To`-Header zuzuordnen.

### Adressänderung
Eine Situation, die eintritt, wenn ein XLS-Import für ein bekanntes Mitglied (gleiche `Pers.-Nr.`) eine andere E-Mail-Adresse enthält als die zuletzt gespeicherte. Der Verifikationsstatus wird nicht automatisch zurückgesetzt – stattdessen wird das Flag `adresse_geaendert` gesetzt, damit der Admin die Situation manuell bewertet.

### Nachweis
Ein eingehendes E-Mail mit einem Untersuchungsdokument (PDF oder Bild), das über IMAP empfangen und als Task verarbeitet wird. Ein eingehender Nachweis gilt auch als implizite Bestätigung der E-Mail-Adresse des Absenders, sofern das Mitglied zugeordnet werden kann.

### IMAP-Verifikationsordner
Ein IMAP-Unterordner (konfigurierbar, Standard: `u-checker-verifikation`), in den verarbeitete Antworten auf Verifikationsmails automatisch verschoben werden. Der Ordner wird beim ersten Bedarf automatisch angelegt. Dient der Übersicht im Postfach – verarbeitete Mails bleiben nicht im Posteingang.

### Analyse
Der erste Schritt im Fälligkeiten-Workflow: Die App liest die aktuelle XLS-Datei ein und berechnet, welche Mitglieder eine oder mehrere relevante Prüfungen im Status `warnung` oder `abgelaufen` haben. Das Ergebnis ist eine Vorschau – noch keine E-Mail wird versendet. Die Analyse ist zustandslos und wird beim Senden automatisch wiederholt.

### Erinnerung
Ein protokollierter Versand einer Fälligkeits-E-Mail an ein Mitglied für einen bestimmten Prüfungstyp. Jede Erinnerung ist ein eigenständiger Log-Eintrag mit Zeitstempel, Prüfungstyp und Status zum Zeitpunkt des Versands. Nicht zu verwechseln mit einer Verifikationsmail – eine Erinnerung betrifft Untersuchungsfristen, keine E-Mail-Adressen.

### Lauf
Veraltet. Bezeichnete früher die kombinierte Analyse+Versand-Operation als eine atomare Einheit. Seit Einführung des 2-Schritt-Flows (Analyse → Auswahl → Erinnerungsversand) kein eigenständiges Konzept mehr. Wird durch Analyse und Erinnerung ersetzt.

### Task
Eine eingehende Nachricht im IMAP-Postfach, die als Nachweis-Eingang erkannt und zur manuellen Bearbeitung übernommen wurde. Antworten auf Verifikationsmails erzeugen keine Tasks. Das vollständige Raw-Email wird als BLOB in der Datenbank gespeichert – die DB ist die autoritative Quelle, nicht das IMAP-Postfach. Zusätzlich wird die IMAP-UID gespeichert, um den Task später im Postfach wiederfinden und verschieben zu können.

### Task-Status
Ein Task durchläuft folgende Zustände:
- `NEU` – Eingang verarbeitet, Mitglied zugeordnet oder nicht zuordenbar, wartet auf manuelle Bestätigung.
- `UNKLARE_ZUORDNUNG` – Automatische Mitgliedszuordnung hat die Mindestsicherheit nicht erreicht; manuelles Zuordnen erforderlich.
- `ERLEDIGT` – Vom Benutzer manuell bestätigt. Der Task erscheint nur noch im Archiv, nicht mehr im Nachweis-Posteingang.

Kein Status `SPAM` – Spam-Eingänge werden direkt gelöscht (kein Archiveintrag).

### Nachweis-Posteingang
Die dedizierte App-Seite (`/nachweise`), die ausschließlich Tasks im Status `NEU` oder `UNKLARE_ZUORDNUNG` anzeigt. Erledigte Tasks erscheinen hier nicht. Jeder Task wird als Karte dargestellt. Nicht zu verwechseln mit dem IMAP-Posteingang (INBOX), der die technische Eingangsquelle ist.

### IMAP-Nachweis-Ordner
Ein IMAP-Unterordner (Name konfigurierbar, Standard: `Nachweise`), in den eine Email verschoben wird, wenn ihr zugehöriger Task als `ERLEDIGT` markiert wird. Beim Wiedereröffnen eines Tasks wird die Email zurück in die INBOX verschoben. Beim Löschen (Spam) wird die Email aus der INBOX gelöscht. Alle IMAP-Operationen sind best-effort: ein Fehler wird geloggt, blockiert aber die DB-Aktion nicht.

### Spam
Ein Task, der beim Eingang irrtümlich als potenzieller Nachweis aufgenommen wurde, aber keinen verwertbaren Inhalt enthält (z.B. Werbemail, irrelevante Nachricht). Spam wird hart gelöscht: Task entfernt aus der DB, Email gelöscht aus dem IMAP-Postfach. Es gibt keinen Archiveintrag. Nicht zu verwechseln mit einem unklaren Nachweis (`UNKLARE_ZUORDNUNG`), der sehr wohl ein echter Nachweis ist.
