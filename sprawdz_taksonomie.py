# -*- coding: utf-8 -*-
"""
sprawdz_taksonomie.py — skąd streszczacz bierze taksonomię?

PO CO OSOBNY SKRYPT
  Automat streszczający jest idempotentny: gdy nie ma zaległości, kończy pracę,
  zanim w ogóle zbuduje prompt — a więc nie dotyka taksonomii i niczego nie
  dowodzi. Ten skrypt pyta moduł wprost.

  Nie wykonuje żadnego zapytania do OpenRoutera, więc nie zużywa limitów
  i można go uruchamiać dowolnie często.

URUCHOMIENIE
  export SUPABASE_DB_URL="postgresql://..."
  python3 sprawdz_taksonomie.py
"""

from __future__ import annotations

import sys

import streszczacz_openrouter as sopen

PODATKI = ["CIT", "VAT", "PIT", "AKCYZA", "PCC"]


def main() -> int:
    print("=" * 74)
    print("ŹRÓDŁO TAKSONOMII")
    print("=" * 74)

    # wymus=True pomija bufor — chcemy zobaczyć stan faktyczny, a nie to,
    # co moduł zapamiętał wcześniej.
    zrodlo = sopen.zaladuj_taksonomie(wymus=True)

    print(f"\n  Taksonomia pochodzi z: {zrodlo.upper()}\n")

    if zrodlo != "baza":
        print("  UWAGA: moduł działa na stałych zaszytych w kodzie.")
        print("  Streszczanie DZIAŁA POPRAWNIE — to zamierzony fallback — ale")
        print("  zmiany taksonomii wprowadzone w bazie nie będą uwzględniane.")
        print("\n  Najczęstsze przyczyny:")
        print("    * brak SUPABASE_DB_URL w środowisku uruchomienia,")
        print("    * tabele taksonomii puste (nie uruchomiono zasilenia),")
        print("    * baza nie odpowiada — komunikat powinien być wyżej.\n")

    b = sopen.branze()
    print("-" * 74)
    print(f"BRANŻE: {len(b)}")
    print("-" * 74)
    for i, x in enumerate(b):
        print(f"  {i:>2}. {x}")

    print()
    print("-" * 74)
    print("PRZEDMIOTY")
    print("-" * 74)
    razem = 0
    for p in PODATKI:
        lista = sopen.przedmioty(p)
        razem += len(lista)
        pierwszy = lista[0] if lista else "(brak)"
        ostatni = lista[-1] if lista else "(brak)"
        print(f"  {p:<7} {len(lista):>3}   od „{pierwszy}”")
        print(f"  {'':<7} {'':<3}   do „{ostatni}”")
    print(f"\n  RAZEM: {razem}")

    # Kolejność ma znaczenie merytoryczne (walidacja zwraca pierwsze trafienie,
    # model dostaje listę w tej kolejności), więc pokazujemy skrajne pozycje —
    # przestawiona lista rzuca się wtedy w oczy.

    print()
    print("-" * 74)
    print("PROMPT SYSTEMOWY — długości")
    print("-" * 74)
    for p in PODATKI:
        print(f"  {p:<7} {len(sopen._system_dla(p)):>6} znaków")
    print("\n  Wartości powinny być zbliżone do: CIT 2624, VAT 2553,")
    print("  PIT 2572, AKCYZA 2419 — tyle wychodziło przed przeniesieniem")
    print("  taksonomii do bazy. Wyraźna różnica oznacza, że listy w bazie")
    print("  nie odpowiadają tym z kodu.")

    return 0 if zrodlo == "baza" else 1


if __name__ == "__main__":
    sys.exit(main())
