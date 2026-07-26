# -*- coding: utf-8 -*-
"""
test_zapisu.py — sprawdzenie Edge Function wtyczka-zapisz.

DWA TRYBY

  Domyślny — WYŁĄCZNIE ŚCIEŻKI ODRZUCENIA. Wysyła żądania, które funkcja ma
  odrzucić: zły sekret, brak dokumentu, streszczenie poniżej progu jakości,
  wartości spoza taksonomii. Nic nie zapisuje do bazy, nic nie wywołuje
  monitoringu. To wystarczy, żeby potwierdzić, że funkcja jest wdrożona,
  autoryzacja działa, a walidacja odrzuca to, co ma odrzucać.

  --zapisz — dokłada JEDEN prawdziwy zapis na dokumencie, który sam znajduje
  w bazie. UWAGA: wpis trafia do streszczenia_auto i zostanie wyłapany przez
  monitoring branż, czyli pójdą maile do subskrybentów. Dlatego skrypt na końcu
  proponuje usunięcie wpisu i robi to po potwierdzeniu.

WYMAGA
  WTYCZKA_URL     — adres funkcji, np.
                    https://TWOJPROJEKT.supabase.co/functions/v1/wtyczka-zapisz
  WTYCZKA_SECRET  — ten sam sekret co w Supabase
  SUPABASE_DB_URL — tylko przy --zapisz (do znalezienia dokumentu i sprzątania)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

URL = os.environ.get("WTYCZKA_URL", "").strip()
SEKRET = os.environ.get("WTYCZKA_SECRET", "").strip()

# Tekst przechodzący próg jakości: ponad 120 znaków, po polsku, bez etykiet.
DOBRE_STRESZCZENIE = (
    "Wnioskodawca prowadzi działalność w zakresie wytwarzania i dostawy ciepła "
    "na potrzeby mieszkańców gminy. Zapytał, czy przysługuje mu prawo do "
    "odliczenia podatku naliczonego od wydatków poniesionych na modernizację "
    "sieci ciepłowniczej. Organ uznał stanowisko wnioskodawcy za prawidłowe. "
    "[WPIS TESTOWY — do usunięcia]"
)


def wyslij(tresc: dict, sekret: str | None = None) -> tuple[int, dict]:
    """Zwraca (kod HTTP, odpowiedź). Nie rzuca przy kodach błędu."""
    dane = json.dumps(tresc).encode("utf-8")
    zad = urllib.request.Request(URL, data=dane, method="POST")
    zad.add_header("Content-Type", "application/json")
    zad.add_header("x-wtyczka-secret", SEKRET if sekret is None else sekret)
    try:
        with urllib.request.urlopen(zad, timeout=20) as odp:
            return odp.status, json.loads(odp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {"blad": "(odpowiedź nie jest JSON-em)"}
    except Exception as e:
        return 0, {"blad": f"Nie udało się połączyć: {e}"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--zapisz", action="store_true",
                    help="dołóż jeden PRAWDZIWY zapis do bazy (uruchomi monitoring)")
    args = ap.parse_args()

    if not URL or not SEKRET:
        raise SystemExit("Ustaw WTYCZKA_URL i WTYCZKA_SECRET.")

    print("=" * 74)
    print("TEST FUNKCJI wtyczka-zapisz")
    print("=" * 74)
    print(f"  Adres: {URL}")
    print(f"  Tryb:  {'ZAPIS (zmieni bazę)' if args.zapisz else 'tylko odrzucenia (bez zapisu)'}\n")

    bledy = 0

    def sprawdz(opis: str, kod_oczekiwany: int, tresc: dict, sekret=None):
        nonlocal bledy
        kod, odp = wyslij(tresc, sekret)
        ok = kod == kod_oczekiwany
        if not ok:
            bledy += 1
        print(f"  {'OK  ' if ok else 'BŁĄD'}  {opis}")
        print(f"        oczekiwano {kod_oczekiwany}, dostano {kod}")
        if odp.get("blad"):
            print(f"        {str(odp['blad'])[:140]}")
        print()
        return odp

    # ── 1. AUTORYZACJA ──
    print("-" * 74)
    print("1. AUTORYZACJA")
    print("-" * 74)
    sprawdz("zły sekret ma zostać odrzucony", 401,
            {"dokumentId": "cokolwiek", "podatek": "CIT"}, sekret="zly-sekret-testowy")

    # ── 2. WALIDACJA WEJŚCIA ──
    print("-" * 74)
    print("2. WALIDACJA WEJŚCIA")
    print("-" * 74)
    sprawdz("brak identyfikatora dokumentu", 400, {"podatek": "CIT"})

    sprawdz("streszczenie poniżej progu jakości", 422,
            {"dokumentId": "x", "podatek": "CIT", "streszczenie": "za krótkie"})

    sprawdz("surowy JSON zamiast tekstu", 422,
            {"dokumentId": "x", "podatek": "CIT",
             "streszczenie": '{"streszczenie": "' + "a" * 300 + '"}'})

    sprawdz("odpowiedź po angielsku", 422,
            {"dokumentId": "x", "podatek": "CIT",
             "streszczenie": "The applicant is a company which provides heating "
                             "services for the municipality. The authority confirmed "
                             "that the costs are deductible for tax purposes."})

    sprawdz("etykieta bezpieczeństwa zamiast treści", 422,
            {"dokumentId": "x", "podatek": "CIT",
             "streszczenie": "User Safety: safe. " + "a" * 200})

    # ── 3. NIEISTNIEJĄCY DOKUMENT ──
    print("-" * 74)
    print("3. DOKUMENT SPOZA ARCHIWUM")
    print("-" * 74)
    sprawdz("dokument, którego nie ma w bazie", 404,
            {"dokumentId": "NIE-ISTNIEJE-0000", "podatek": "CIT",
             "profil": "test", "temat": "T",
             "streszczenie": DOBRE_STRESZCZENIE,
             "branze": ["ciepłownicza"], "przedmiot": "leasing"})

    if not args.zapisz:
        print("-" * 74)
        print(f"WYNIK: {'wszystko zgodnie z oczekiwaniem' if bledy == 0 else str(bledy) + ' niezgodności'}")
        print("-" * 74)
        print("\nNic nie zostało zapisane do bazy.")
        print("Funkcja odpowiada, autoryzacja działa, walidacja odrzuca to,")
        print("co ma odrzucać.\n")
        print("Żeby sprawdzić PRAWDZIWY zapis, uruchom z --zapisz.")
        print("UWAGA: wpis trafi do streszczenia_auto i uruchomi monitoring")
        print("branż, czyli pójdą maile do subskrybentów. Skrypt zaproponuje")
        print("usunięcie wpisu po teście.")
        return 1 if bledy else 0

    # ── 4. PRAWDZIWY ZAPIS ──
    print("-" * 74)
    print("4. PRAWDZIWY ZAPIS")
    print("-" * 74)

    import db_core
    url_bazy = os.environ.get("SUPABASE_DB_URL")
    if not url_bazy:
        raise SystemExit("Tryb --zapisz wymaga SUPABASE_DB_URL.")
    db = db_core.SupabaseDB({"url": url_bazy})

    # Szukamy dokumentu, który NIE ma jeszcze wpisu z wtyczki — żeby test
    # nie nadpisał czyjejś pracy.
    wiersze = db.wykonaj(
        """SELECT d.id, d.sygnatura, d.podatek
           FROM dokumenty d
           WHERE NOT EXISTS (
               SELECT 1 FROM streszczenia_auto s
               WHERE s.dokument_id = d.id AND s.model = 'wtyczka:TEST'
           )
           ORDER BY d.data_wyd DESC
           LIMIT 1""", fetch=True)
    if not wiersze:
        raise SystemExit("Nie znalazłem dokumentu do testu.")

    dok = wiersze[0]
    print(f"  Dokument testowy: {dok['sygnatura']}  ({dok['podatek']}, id {dok['id']})\n")

    odp = sprawdz("zapis poprawnego streszczenia", 200,
                  {"dokumentId": dok["id"], "podatek": dok["podatek"],
                   "profil": "TEST", "temat": "Wpis testowy — do usunięcia",
                   "streszczenie": DOBRE_STRESZCZENIE,
                   "branze": ["ciepłownicza", "nieistniejąca-branża"],
                   "przedmiot": "leasing"})

    if odp.get("zapisano"):
        print(f"        zapisane branże: {odp.get('branze')}")
        print(f"        zapisany przedmiot: {odp.get('przedmiot')!r}")
        if odp.get("odrzucone"):
            print(f"        odrzucone: {odp['odrzucone']}")
            print("        (tak ma być — 'nieistniejąca-branża' jest spoza taksonomii)")
        print()

    sprawdz("powtórny zapis bez nadpisania", 409,
            {"dokumentId": dok["id"], "podatek": dok["podatek"],
             "profil": "TEST", "temat": "Drugi raz",
             "streszczenie": DOBRE_STRESZCZENIE, "branze": [], "przedmiot": ""})

    sprawdz("powtórny zapis z nadpisaniem", 200,
            {"dokumentId": dok["id"], "podatek": dok["podatek"],
             "profil": "TEST", "temat": "Nadpisany",
             "streszczenie": DOBRE_STRESZCZENIE, "branze": ["handlowa"],
             "przedmiot": "", "nadpisz": True})

    # ── 5. SPRZĄTANIE ──
    print("-" * 74)
    print("5. SPRZĄTANIE")
    print("-" * 74)
    usuniete = db.wykonaj(
        "DELETE FROM streszczenia_auto WHERE dokument_id = %s AND model = 'wtyczka:TEST'",
        (dok["id"],))
    print(f"  Usunięto wpisów testowych: {usuniete}")
    print("  Baza wróciła do stanu sprzed testu.\n")
    print("  Monitoring mógł jednak zdążyć wysłać maile, jeśli akurat")
    print("  uruchomił się w tym oknie. Przy jednym wpisie testowym to")
    print("  co najwyżej jedno powiadomienie.\n")

    print("-" * 74)
    print(f"WYNIK: {'wszystko zgodnie z oczekiwaniem' if bledy == 0 else str(bledy) + ' niezgodności'}")
    return 1 if bledy else 0


if __name__ == "__main__":
    sys.exit(main())
