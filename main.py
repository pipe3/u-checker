#!/usr/bin/env python3
import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from u_checker import check_examinations, send_notifications, send_summary
from u_checker.checker import PRUEFUNGSTYPEN, WARN_DAYS


def main():
    parser = argparse.ArgumentParser(
        description="Prüft ablaufende Untersuchungen aus MP-Feuer Export und sendet Benachrichtigungen."
    )
    parser.add_argument("excel_file", help="Pfad zur exportierten XLS-Datei aus MP-Feuer")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Emails im Terminal anzeigen ohne zu versenden",
    )
    args = parser.parse_args()

    filepath = Path(args.excel_file)
    if not filepath.exists():
        print(f"Fehler: Datei nicht gefunden: {filepath}", file=sys.stderr)
        sys.exit(1)

    print(f"Datei:          {filepath.name}")
    print(f"Prüfungstypen:  {', '.join(PRUEFUNGSTYPEN)}")
    print(f"Warnfrist:      {WARN_DAYS} Tage")
    if args.dry_run:
        print("Modus:          DRY-RUN (kein Versand)")
    print()

    persons = check_examinations(str(filepath))

    if args.dry_run:
        print(f"{len(persons)} Person(en) mit Handlungsbedarf gefunden.")

    send_notifications(persons, dry_run=args.dry_run)
    send_summary(persons, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
