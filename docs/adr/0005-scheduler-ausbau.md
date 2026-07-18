# ADR 0005: Scheduler wird ausgebaut

**Status:** Accepted

## Context

Die App enthält einen Scheduler (`scheduler.py`), der `_do_run` automatisch ausführen kann. Da es keinen automatischen XLS-Import aus MP-Feuer gibt (nur manueller Upload), würde ein automatischer Versand auf einer veralteten XLS-Datei basieren. Zusätzlich widerspricht automatischer Versand dem Prinzip, dass der Benutzer jeden Versand explizit bestätigen will (siehe ADR-0004).

## Decision

Der Scheduler wird vollständig entfernt. Alle Fälligkeits-E-Mails werden ausschließlich manuell über den 2-Schritt-Flow auf `/faelligkeiten` ausgelöst.

## Consequences

- Kein Risiko eines Versands auf Basis veralteter Daten.
- Der Benutzer muss selbst daran denken, die Analyse periodisch anzustoßen.
- `scheduler.py` und die zugehörige Konfiguration entfallen.
