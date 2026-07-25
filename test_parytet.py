# -*- coding: utf-8 -*-
"""
test_parytet.py — dowód, że Python i JavaScript normalizują IDENTYCZNIE.

To najważniejszy test fazy 0. Cała integracja opiera się na założeniu, że klucz
policzony przez wtyczkę (JS) jest tym samym łańcuchem, co klucz zapisany
w bazie (Python). Rozjazd o jeden znak = ciche pudła, których nikt nie zauważy,
bo wtyczka po prostu powie „nie ma w archiwum”.

Korpus obejmuje realne warianty zapisu ORAZ celowe złośliwości: miękkie dywizy
w środku sygnatury (przeglądarka wstawia je przy łamaniu wiersza), twarde
spacje, wszystkie warianty myślnika, polskie znaki, pełną szerokość.

Uruchomienie:  python3 test_parytet.py
"""

from __future__ import annotations

import json
import subprocess
import sys

import normalizacja_sygnatur as ns

# ---------------------------------------------------------------------------
# KORPUS
# ---------------------------------------------------------------------------
KORPUS = [
    # ── KIS: postać wzorcowa i jej warianty zapisu ──
    "0114-KDIP2-2.4010.123.2026.1.AS",
    "0114-kdip2-2.4010.123.2026.1.as",
    "  0114-KDIP2-2.4010.123.2026.1.AS  ",
    "0114-KDIP2-2.4010.123.2026.1.AS.",
    "„0114-KDIP2-2.4010.123.2026.1.AS”",
    "0114\u2011KDIP2\u20112.4010.123.2026.1.AS",      # dywiz niełamliwy
    "0114\u2013KDIP2\u20132.4010.123.2026.1.AS",      # półpauza
    "0114\u2014KDIP2\u20142.4010.123.2026.1.AS",      # pauza
    "0114\u2212KDIP2\u22122.4010.123.2026.1.AS",      # minus matematyczny
    "0114-KDIP2-2.4010.\u00ad123.2026.1.AS",          # miękki dywiz w środku
    "0114-KDIP2-2.4010.123.2026.1.AS\u200b",          # spacja zerowej szerokości
    "\ufeff0114-KDIP2-2.4010.123.2026.1.AS",          # BOM na początku
    "0114-KDIP2-2.4010.123.2026.1.\u00a0AS",          # twarda spacja
    "0111-KDIB3-1.4012.55.2026.2.KO",
    "0115-KDIT1.4011.900.2025.1.MR",
    "0113-KDIPT2-3.4011.1.2026.10.KP",

    # ── Sądy administracyjne ──
    "I FSK 1234/23",
    "i fsk 1234/23",
    "I  FSK   1234/23",
    "II FSK 45/24",
    "III SA/Wa 1234/23",
    "I SA/Lu 123/24",
    "I SA/\u0141d 55/26",                              # Łódź, polski znak
    "I SA/Gl 1/2026",
    "I\u00a0FSK\u00a01234/23",                        # twarde spacje

    # ── Starsze oznaczenia izb skarbowych ──
    "ITPB3/423-123/14/AB",
    "IPPB5/4510-1/16-2/AK",

    # ── Przypadki brzegowe ──
    "",
    "   ",
    ".",
    "...",
    "\u00ad\u00ad\u00ad",
    "0114-KDIP2-2.4010.123.2026.1",                   # bez inicjałów
    "Ａ１２３",                                        # pełna szerokość (NFKC)
    "sygnatura: 0114-KDIP2-2.4010.1.2026.1.AS",
    "ą ć ę ł ń ó ś ź ż",
    "ĄĆĘŁŃÓŚŹŻ",
    "0114-KDIP2-2.4010.123.2026.1.AS oraz I FSK 1/23",
    # Przypadek z prawdziwego przebiegu: dwie sygnatury bez separatora.
    "II FSK 992/23 0113-KDIPT1-3.4012.513.2026.1.MK",
]


def wyniki_python(korpus: list[str]) -> list[dict]:
    return [{
        "wejscie": s,
        "norm":    ns.normalizuj_sygnature(s),
        "bez_ini": ns.klucz_bez_inicjalow(ns.normalizuj_sygnature(s)),
        "rodzaj":  ns.rodzaj_sygnatury(ns.normalizuj_sygnature(s)),
        "podejrz": ns.podejrzany_klucz(ns.normalizuj_sygnature(s)),
    } for s in korpus]


def wyniki_node(korpus: list[str]) -> list[dict]:
    """Odpala Node z jednorazowym skryptem liczącym to samo w JS."""
    skrypt = """
const S = require('./sygnatury.js');
let dane = '';
process.stdin.on('data', c => dane += c);
process.stdin.on('end', () => {
  const korpus = JSON.parse(dane);
  const out = korpus.map(s => {
    const n = S.normalizuj(s);
    return { wejscie: s, norm: n, bez_ini: S.bezInicjalow(n),
             rodzaj: S.rodzaj(n), podejrz: S.podejrzany(n) };
  });
  process.stdout.write(JSON.stringify(out));
});
"""
    with open("_parytet_tmp.js", "w", encoding="utf-8") as f:
        f.write(skrypt)

    proc = subprocess.run(
        ["node", "_parytet_tmp.js"],
        input=json.dumps(korpus, ensure_ascii=False),
        capture_output=True, text=True, encoding="utf-8",
    )
    if proc.returncode != 0:
        raise SystemExit(f"Node zwrócił błąd:\n{proc.stderr}")
    return json.loads(proc.stdout)


def widoczny(s: str) -> str:
    """Zapis z ujawnionymi znakami niewidocznymi — inaczej raport o rozjeździe
    pokazywałby dwa łańcuchy wyglądające identycznie."""
    podmiany = {
        "\u00ad": "<SHY>", "\u200b": "<ZWSP>", "\ufeff": "<BOM>",
        "\u00a0": "<NBSP>", "\u2011": "<-NB>", "\u2013": "<EN>",
        "\u2014": "<EM>", "\u2212": "<MINUS>", "\t": "<TAB>",
    }
    for a, b in podmiany.items():
        s = s.replace(a, b)
    return s


def main() -> int:
    py = wyniki_python(KORPUS)
    js = wyniki_node(KORPUS)

    if len(py) != len(js):
        print(f"BŁĄD KRYTYCZNY: różna liczba wyników ({len(py)} vs {len(js)})")
        return 1

    rozjazdy = []
    for a, b in zip(py, js):
        for pole in ("norm", "bez_ini", "rodzaj", "podejrz"):
            if a[pole] != b[pole]:
                rozjazdy.append((a["wejscie"], pole, a[pole], b[pole]))

    print("=" * 74)
    print("TEST PARYTETU  Python  <->  JavaScript")
    print("=" * 74)
    print(f"Pozycji w korpusie: {len(KORPUS)}")
    print(f"Porównań (4 pola na pozycję): {len(KORPUS) * 4}")
    print()

    if rozjazdy:
        print(f"ROZJAZDY: {len(rozjazdy)}\n")
        for wej, pole, w_py, w_js in rozjazdy:
            print(f"  wejście : {widoczny(wej)!r}")
            print(f"  pole    : {pole}")
            print(f"  Python  : {widoczny(w_py)!r}")
            print(f"  JS      : {widoczny(w_js)!r}")
            print()
        return 1

    print("PARYTET POTWIERDZONY — wszystkie wyniki identyczne.\n")

    print("-" * 74)
    print("PRZEGLĄD NORMALIZACJI (wejście -> klucz kanoniczny [rodzaj])")
    print("-" * 74)
    for r in py:
        wej = widoczny(r["wejscie"])
        if len(wej) > 42:
            wej = wej[:39] + "..."
        print(f"  {wej:<44} -> {r['norm'] or '(pusty)':<34} [{r['rodzaj']}]")

    # ── KLASY RÓWNOWAŻNOŚCI ────────────────────────────────────────────────
    # Właściwy sens modułu: zapisy uznane za ten sam dokument muszą dać jeden
    # klucz. Klasy wypisujemy JAWNIE — wcześniejsza wersja tego testu zgadywała
    # przynależność po fragmencie łańcucha i wciągała do klasy pozycje, które
    # nigdy nie miały się skleić (sygnaturę bez inicjałów oraz zdanie z dwiema
    # sygnaturami). Test przechodził wtedy z fałszywego powodu albo padał
    # z powodu własnego błędu, nie błędu kodu.
    print()
    print("-" * 74)
    print("KLASY RÓWNOWAŻNOŚCI (te zapisy muszą dać jeden klucz)")
    print("-" * 74)

    klasy = {
        "KIS 0114-KDIP2-2.4010.123.2026.1.AS": [
            "0114-KDIP2-2.4010.123.2026.1.AS",
            "0114-kdip2-2.4010.123.2026.1.as",
            "  0114-KDIP2-2.4010.123.2026.1.AS  ",
            "0114-KDIP2-2.4010.123.2026.1.AS.",
            "„0114-KDIP2-2.4010.123.2026.1.AS”",
            "0114\u2011KDIP2\u20112.4010.123.2026.1.AS",
            "0114\u2013KDIP2\u20132.4010.123.2026.1.AS",
            "0114\u2014KDIP2\u20142.4010.123.2026.1.AS",
            "0114\u2212KDIP2\u22122.4010.123.2026.1.AS",
            "0114-KDIP2-2.4010.\u00ad123.2026.1.AS",
            "0114-KDIP2-2.4010.123.2026.1.AS\u200b",
            "\ufeff0114-KDIP2-2.4010.123.2026.1.AS",
            "0114-KDIP2-2.4010.123.2026.1.\u00a0AS",
        ],
        "Wyrok I FSK 1234/23": [
            "I FSK 1234/23",
            "i fsk 1234/23",
            "I  FSK   1234/23",
            "I\u00a0FSK\u00a01234/23",
        ],
    }

    bledy_klas = 0
    for nazwa, zapisy in klasy.items():
        klucze = {ns.normalizuj_sygnature(z) for z in zapisy}
        if len(klucze) == 1:
            print(f"  OK   {nazwa}")
            print(f"       {len(zapisy)} zapisów -> {klucze.pop()}")
        else:
            bledy_klas += 1
            print(f"  BŁĄD {nazwa} — rozjazd na {len(klucze)} kluczy:")
            for k in sorted(klucze):
                print(f"       {k}")

    # ── ROZRÓŻNIALNOŚĆ ─────────────────────────────────────────────────────
    # Druga strona medalu: normalizacja nie może SKLEIĆ dokumentów różnych.
    # Bez tej kontroli „normalizator”, który zwraca pusty łańcuch dla
    # wszystkiego, przeszedłby klasy równoważności śpiewająco.
    print()
    print("-" * 74)
    print("ROZRÓŻNIALNOŚĆ (te zapisy NIE mogą się skleić)")
    print("-" * 74)

    musza_sie_roznic = [
        ("wersja 1 vs wersja 2 tego samego pisma",
         "0114-KDIP2-2.4010.123.2026.1.AS", "0114-KDIP2-2.4010.123.2026.2.AS"),
        ("różny numer sprawy",
         "0114-KDIP2-2.4010.123.2026.1.AS", "0114-KDIP2-2.4010.124.2026.1.AS"),
        ("różny rok",
         "0114-KDIP2-2.4010.123.2025.1.AS", "0114-KDIP2-2.4010.123.2026.1.AS"),
        ("różny dział (CIT vs VAT)",
         "0114-KDIP2-2.4010.123.2026.1.AS", "0114-KDIP2-2.4012.123.2026.1.AS"),
        ("różny sąd przy tym samym numerze",
         "I SA/Lu 123/24", "I SA/Gl 123/24"),
        ("różna izba",
         "I FSK 1234/23", "II FSK 1234/23"),
    ]

    for opis, a, b in musza_sie_roznic:
        ka, kb = ns.normalizuj_sygnature(a), ns.normalizuj_sygnature(b)
        if ka != kb:
            print(f"  OK   {opis}")
        else:
            bledy_klas += 1
            print(f"  BŁĄD {opis} — oba dały {ka}")

    if bledy_klas:
        print(f"\n{bledy_klas} błędów w klasach.")
        return 1

    print("\nWSZYSTKO PRZESZŁO.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
