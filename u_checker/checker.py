import os
import xlrd
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Dict, List, Optional

_DEFAULT_WARN_DAYS = int(os.getenv("WARN_DAYS", "90"))
_DEFAULT_PRUEFUNGSTYPEN = [t.strip() for t in os.getenv("PRUEFUNGSTYPEN", "G25").split(",")]

# Modul-Globals für Legacy-Zugriff (z.B. monkeypatch in Tests)
WARN_DAYS = _DEFAULT_WARN_DAYS
PRUEFUNGSTYPEN = _DEFAULT_PRUEFUNGSTYPEN

# Spaltenindizes aus MP-Feuer Export
COL_TYP = 0          # Kurzbezeich.
COL_BESCHREIBUNG = 1 # Prüfung
COL_DATUM = 4        # Datum
COL_GUELTIG_BIS = 6  # Gültig bis
COL_OK = 7           # OK
COL_PERS_NR = 18     # Pers.-Nr.
COL_VORNAME = 20     # Vorname
COL_NACHNAME = 21    # Nachname
COL_EMAIL = 33       # E-Mail privat
COL_EI_ANZEIGEN = 42 # bei EI anzeigen (Nein = ausgeschieden)


@dataclass
class Pruefung:
    typ: str
    beschreibung: str
    datum: date
    status: str  # 'warnung' | 'abgelaufen'


@dataclass
class Person:
    pers_nr: str
    vorname: str
    nachname: str
    email: str
    pruefungen: List[Pruefung] = field(default_factory=list)
    cc_force: bool = field(default=False, compare=False, repr=False)

    @property
    def hat_abgelaufene(self) -> bool:
        return self.cc_force or any(p.status == "abgelaufen" for p in self.pruefungen)


def _xl_to_date(wb: xlrd.Book, val) -> Optional[date]:
    if not val:
        return None
    t = xlrd.xldate_as_tuple(val, wb.datemode)
    return date(t[0], t[1], t[2])


def check_examinations(
    filepath: str,
    *,
    warn_days: Optional[int] = None,
    pruefungstypen: Optional[List[str]] = None,
) -> List[Person]:
    effective_warn_days = warn_days if warn_days is not None else WARN_DAYS
    effective_pruefungstypen = pruefungstypen if pruefungstypen is not None else PRUEFUNGSTYPEN

    wb = xlrd.open_workbook(filepath)
    sh = wb.sheets()[0]
    heute = date.today()

    # Pro (pers_nr, typ): alle offenen Einträge sammeln
    entries: Dict[tuple, List[dict]] = {}

    for r in range(1, sh.nrows):
        row = sh.row_values(r)
        typ = str(row[COL_TYP]).strip()
        ok = str(row[COL_OK]).strip()

        if typ not in effective_pruefungstypen:
            continue
        if ok == "Ja":
            continue
        if str(row[COL_EI_ANZEIGEN]).strip() == "Nein":
            continue

        datum = _xl_to_date(wb, row[COL_DATUM])
        gueltig_bis = _xl_to_date(wb, row[COL_GUELTIG_BIS])
        relevant = gueltig_bis if gueltig_bis else datum

        if not relevant:
            continue

        key = (str(row[COL_PERS_NR]).strip(), typ)
        entries.setdefault(key, []).append({
            "datum": relevant,
            "beschreibung": str(row[COL_BESCHREIBUNG]).strip(),
            "row": row,
        })

    persons: Dict[str, Person] = {}

    for (pers_nr, typ), kandidaten in entries.items():
        # Neuester Eintrag = aktuelle Fälligkeit
        latest = max(kandidaten, key=lambda e: e["datum"])
        datum = latest["datum"]
        row = latest["row"]

        if datum <= heute:
            status = "abgelaufen"
        elif datum <= heute + timedelta(days=effective_warn_days):
            status = "warnung"
        else:
            continue

        email = str(row[COL_EMAIL]).strip()
        if not email:
            print(f"WARNUNG: Keine E-Mail-Adresse für {row[COL_VORNAME]} {row[COL_NACHNAME]} (Nr. {pers_nr}) – übersprungen.")
            continue

        if pers_nr not in persons:
            persons[pers_nr] = Person(
                pers_nr=pers_nr,
                vorname=str(row[COL_VORNAME]).strip(),
                nachname=str(row[COL_NACHNAME]).strip(),
                email=email,
            )

        persons[pers_nr].pruefungen.append(Pruefung(
            typ=typ,
            beschreibung=latest["beschreibung"],
            datum=datum,
            status=status,
        ))

    return list(persons.values())
