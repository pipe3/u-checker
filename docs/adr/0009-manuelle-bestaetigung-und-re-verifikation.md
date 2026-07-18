# ADR 0009: Manuelle Bestätigung durch Admin + eigener Status für erneute Verifikation

**Status:** Accepted

## Context

Zwei beobachtete Fälle führen dazu, dass ein Mitglied dauerhaft im Status `ausstehend` hängen bleibt, obwohl die E-Mail-Adresse de facto nachweislich funktioniert:

1. **Versehentliche Re-Verifikation**: Ein Mitglied war bereits `bestaetigt`. Eine neue Verifikationsmail wird (z.B. versehentlich) erneut versendet. Die Person denkt "das habe ich doch schon bestätigt" und reagiert nicht mehr. Ohne Gegenmaßnahme würde der bestehende Status-Automat den alten `bestaetigt`-Zustand beim erneuten Versand durch `ausstehend` ersetzen – die frühere, echte Bestätigung wäre für den Admin nicht mehr sichtbar.
2. **Antwort auf falschem Kanal**: Die Person bestätigt nicht per Reply auf die Verifikationsmail, sondern per Chat oder an die persönliche E-Mail-Adresse des Admins. Der `In-Reply-To`-Abgleich (ADR 0001) greift nicht, da keine passende Nachricht im überwachten Postfach eingeht. Der Status bleibt für immer `ausstehend`, obwohl die Adresse nachweislich aktiv ist.

In beiden Fällen ist der Beweis der Adress-Aktivität real vorhanden, nur außerhalb der vom System erwarteten Kanäle. Analog zu ADR 0003 (Nachweis-Eingang als implizite Bestätigung) ist dies ein weiteres Beispiel für "ein Signal außerhalb des Standardwegs beweist die Bestätigung genauso gut".

Erwogen, aber verworfen: ein automatischer Timeout, der `re_verifikation_ausstehend` nach einer festen Frist stillschweigend wieder auf `bestaetigt` zurückfallen lässt. Verworfen, weil dies dem in ADR 0002 etablierten Prinzip widerspricht, dass der Admin volle Kontrolle über Statusänderungen behält und keine impliziten Automatismen laufen, die eine tatsächliche Problemadresse stillschweigend durchwinken könnten. Ebenso erwogen, aber verworfen: eine Warnung vor dem Versand einer Verifikationsmail an bereits `bestaetigte` Mitglieder – das hätte Fall 1 an der Quelle verhindert, wurde aber als unnötige Friktion bewertet, da der neue Zwischenstatus die Folgen bereits zuverlässig abfängt.

## Decision

**Manuelle Bestätigung**: Die Verifikationsliste bekommt einen Button "Manuell bestätigen", mit dem der Admin ein Mitglied direkt auf `bestaetigt` setzt, ohne dass ein automatischer Abgleich (In-Reply-To oder Nachweis) stattgefunden hat. Der Button ist nur sichtbar, wenn bereits eine Verifikationsmail versendet wurde, also bei den Status `ausstehend` und `re_verifikation_ausstehend` – bei `nie_geprueft` und `ungueltige_adresse` gibt es nichts zu bestätigen.

**Neuer Zwischenstatus `re_verifikation_ausstehend`**: Wird an ein Mitglied im Status `bestaetigt` eine neue Verifikationsmail versendet, springt der Status nicht direkt auf `ausstehend`, sondern auf `re_verifikation_ausstehend`. Dieser Status hält sowohl das alte Bestätigungsdatum als auch das neue Sendedatum vorrätig, sodass der Admin in der Verifikationsliste weiterhin sieht, wann zuletzt tatsächlich bestätigt wurde. Geht auf die neue Verifikationsmail eine passende Antwort ein, greift die normale Logik aus ADR 0001: Status springt auf `bestaetigt`, Datum wird auf den Zeitpunkt der neuen Antwort aktualisiert. Bleibt eine Antwort aus, verbleibt der Status unbegrenzt auf `re_verifikation_ausstehend` – kein automatischer Timeout-Fallback. Der Admin löst den Fall bewusst über den manuellen Bestätigen-Button.

**Herkunfts-Vermerk**: Jede Bestätigung erhält ein grobes Herkunftsmerkmal `automatisch` oder `manuell` (keine feinere Unterscheidung zwischen Antwort und Nachweis). Die UI zeigt bei manueller Bestätigung sichtbar "manuell bestätigt am X", um erkennbar zu machen, dass hier keine Systemvalidierung, sondern eine Admin-Einschätzung vorliegt.

## Consequences

- Beide beobachteten Fälle sind lösbar: Fall 1 landet in `re_verifikation_ausstehend` und wird über den manuellen Button aufgelöst; Fall 2 wird unabhängig vom Status direkt über denselben Button gelöst.
- Kein Datenverlust bei versehentlicher Re-Verifikation: die alte Bestätigung bleibt sichtbar, bis eine neue Antwort eintrifft oder der Admin eingreift.
- Kein impliziter Automatismus, der eine unbeobachtete Fehlkommunikation stillschweigend als bestätigt durchwinkt – konsistent mit ADR 0002.
- Neue Verantwortung beim Admin: hängende `re_verifikation_ausstehend`-Fälle werden nicht von selbst aufgelöst und erfordern aktives Nachsehen in der Verifikationsliste.
- Der manuelle Bestätigen-Button ist ein Vertrauensbruch mit der bisherigen Garantie aus ADR 0001 ("nur eine echte Antwort zählt") – bewusst in Kauf genommen, da der Admin die Verantwortung für die Einschätzung trägt und dies durch den Herkunfts-Vermerk sichtbar bleibt.
