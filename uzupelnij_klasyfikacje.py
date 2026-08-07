# -*- coding: utf-8 -*-
"""
MODUŁ 9: Uzupełnianie klasyfikacji — Skaner Doradca.

Domyka luki w kolumnach `branze` i `przedmiot` tabeli `streszczenia_auto`.
Braki wzięły się stąd, że model gubił taksonomię przy długich sesjach
streszczania: pierwsze dokumenty dostawały klasyfikację, kolejne już nie.

DLACZEGO PÓŁAUTOMAT, A NIE AUTOMAT
    Automat streszczeń (OpenRouter) jest wyłączony na stałe i nie wracamy do
    niego tylnymi drzwiami. To narzędzie tylko SKŁADA polecenie i PRZYJMUJE
    odpowiedź — przez model przepuszczasz ją sam, tak jak przy streszczaniu.

TRZY ZASADY, KTÓRE ZDECYDOWAŁY O KSZTAŁCIE

    1. Jedna partia = jeden podatek.
       Przedmioty są osobną listą dla każdego podatku. Paczka mieszana
       wymagałaby wysłania kilku taksonomii naraz — czyli odtworzenia
       dokładnie tego warunku, w którym model je gubił.

    2. Taksonomia w KAŻDYM poleceniu, nie raz na sesję.
       To jest przyczyna pierwotna braków i jedyna rzecz, która musi się
       zmienić względem dotychczasowego trybu pracy.

    3. Uzupełniamy wyłącznie PUSTE kolumny.
       Model „poprawiający” przy okazji istniejącą klasyfikację po cichu
       zmieniłby dopasowania subskrypcji — a te wysyłają maile.

EGRESS
    Statystyki liczone przez COUNT, bez pobierania rekordów. Partia pobiera
    tylko kolumny potrzebne do klasyfikacji, ze streszczeniem przyciętym do
    ~1200 znaków — pełny `tekst` dokumentu nigdy stąd nie wychodzi.
"""

from __future__ import annotations

import json
import re

import streamlit as st

import archiwum_supabase
import paleta
from streszczacz_openrouter import (
    branze,
    przedmioty,
    zaladuj_taksonomie,
    zrodlo_taksonomii,
    _waliduj_branze,
    _waliduj_przedmioty,
)

# Rozmiar partii. Kilkanaście dokumentów mieści się w jednym oknie kontekstu
# razem z taksonomią i zostawia zapas na odpowiedź. Powyżej dwudziestu wraca
# ryzyko, dla którego to narzędzie w ogóle powstało.
DOMYSLNA_PARTIA = 12
MAKS_PARTIA = 20

# Ile streszczenia wystarczy do klasyfikacji. Pełna treść nie jest potrzebna —
# przedmiot i branża wynikają z pierwszych zdań.
LIMIT_STRESZCZENIA = 1200


def _db():
    return archiwum_supabase._get_db()


def _zapytaj(sql: str, p: tuple | None = None) -> list[dict]:
    return _db().wykonaj(sql, p, fetch=True)


# ---------------------------------------------------------------------------
# STAN BRAKÓW
# ---------------------------------------------------------------------------

def _statystyki() -> list[dict]:
    """Ile brakuje, w rozbiciu na podatek. Same liczby, bez rekordów."""
    return _zapytaj(
        """
        SELECT d.podatek,
               count(*) FILTER (WHERE COALESCE(s.przedmiot, '') = '') AS bez_przedmiotu,
               count(*) FILTER (WHERE COALESCE(s.branze, '')    = '') AS bez_branzy,
               count(*) FILTER (WHERE COALESCE(s.przedmiot, '') = ''
                                  AND COALESCE(s.branze, '')    = '') AS bez_obu,
               count(*) AS wszystkich
        FROM streszczenia_auto s
        JOIN dokumenty d ON d.id = s.dokument_id
        GROUP BY d.podatek
        ORDER BY d.podatek
        """
    )


def _partia(podatek: str, ile: int) -> list[dict]:
    """Kolejne dokumenty bez klasyfikacji. Od najnowszych — te są najczęściej
    potrzebne w bieżącej pracy."""
    return _zapytaj(
        """
        SELECT s.id, d.sygnatura, d.data_wyd, d.podatek,
               COALESCE(s.temat, '') AS temat,
               left(COALESCE(s.streszczenie, ''), %s) AS streszczenie,
               COALESCE(s.przedmiot, '') AS przedmiot,
               COALESCE(s.branze, '')    AS branze
        FROM streszczenia_auto s
        JOIN dokumenty d ON d.id = s.dokument_id
        WHERE d.podatek = %s
          AND (COALESCE(s.przedmiot, '') = '' OR COALESCE(s.branze, '') = '')
        ORDER BY d.data_wyd DESC NULLS LAST, s.id DESC
        LIMIT %s
        """,
        (LIMIT_STRESZCZENIA, podatek, ile),
    )


# ---------------------------------------------------------------------------
# POLECENIE DLA MODELU
# ---------------------------------------------------------------------------

def _polecenie(podatek: str, partia: list[dict]) -> str:
    """Składa polecenie z PEŁNĄ taksonomią i listą dokumentów.

    Taksonomia jest tu w całości, przy każdej partii. To nie jest nadmiar —
    to jedyna zmiana, która odróżnia to narzędzie od trybu pracy, w którym
    braki powstały.
    """
    lista_przedmiotow = przedmioty(podatek)
    lista_branz = branze()

    linie = [
        "Jesteś asystentem polskiego doradcy podatkowego. Klasyfikujesz "
        "interpretacje indywidualne — NIE streszczasz ich i nie komentujesz.",
        "",
        f"PRZEDMIOTY dla podatku {podatek} — wybierz DOKŁADNIE JEDEN z tej listy:",
    ]
    linie += [f"  - {p}" for p in lista_przedmiotow]
    linie += [
        "",
        "BRANŻE — wybierz od jednej do dwóch z tej listy (branża podmiotu, "
        "który wystąpił o interpretację; gdy nie wynika z treści, użyj „inna”):",
    ]
    linie += [f"  - {b}" for b in lista_branz]
    linie += [
        "",
        "ZASADY",
        "  1. Wartości MUSZĄ pochodzić dosłownie z powyższych list. Nie twórz "
        "własnych nazw, nie skracaj, nie tłumacz.",
        "  2. Przedmiot: dokładnie jeden. Branże: jedna albo dwie.",
        "  3. Gdy nie potrafisz przypisać przedmiotu, użyj pozycji „inne …” "
        "z końca listy. Nie zostawiaj pustego pola.",
        "  4. Odpowiadasz WYŁĄCZNIE tablicą JSON, bez komentarza i bez "
        "znaczników ``` — od znaku [ do znaku ].",
        "",
        "FORMAT ODPOWIEDZI",
        '  [{"id": 123, "przedmiot": "…", "branze": ["…"]}, …]',
        "",
        f"DOKUMENTY DO SKLASYFIKOWANIA ({len(partia)}):",
        "",
    ]

    for d in partia:
        braki = []
        if not d["przedmiot"]:
            braki.append("przedmiot")
        if not d["branze"]:
            braki.append("branże")

        linie.append(f'--- id: {d["id"]} | {d["sygnatura"]} '
                     f'| brakuje: {", ".join(braki)} ---')
        if d["temat"]:
            linie.append(f"Temat: {d['temat']}")
        if d["streszczenie"]:
            linie.append(f"Streszczenie: {d['streszczenie']}")
        # Wartość już ustaloną podajemy modelowi jako kontekst, ale prosimy
        # o zwrócenie obu pól — zapis i tak weźmie tylko brakujące.
        if d["przedmiot"]:
            linie.append(f"(przedmiot już ustalony: {d['przedmiot']})")
        if d["branze"]:
            linie.append(f"(branże już ustalone: {d['branze']})")
        linie.append("")

    return "\n".join(linie)


# ---------------------------------------------------------------------------
# ODCZYT ODPOWIEDZI
# ---------------------------------------------------------------------------

def _wyodrebnij_tablice(tresc: str) -> list:
    """Wyciąga tablicę JSON z odpowiedzi.

    Modele lubią dokleić zdanie przed albo znaczniki ``` wokół — tolerujemy
    to, zamiast odsyłać z komunikatem o błędnym formacie.
    """
    t = (tresc or "").strip()
    if not t:
        return []

    t = re.sub(r"^```[a-zA-Z]*\s*", "", t)
    t = re.sub(r"\s*```$", "", t).strip()

    try:
        dane = json.loads(t)
    except Exception:
        poczatek, koniec = t.find("["), t.rfind("]")
        if poczatek < 0 or koniec <= poczatek:
            return []
        try:
            dane = json.loads(t[poczatek:koniec + 1])
        except Exception:
            return []

    if isinstance(dane, dict):
        dane = [dane]
    return dane if isinstance(dane, list) else []


def _sprawdz(odpowiedz: str, partia: list[dict], podatek: str) -> dict:
    """Zestawia odpowiedź modelu z partią i taksonomią.

    Zwraca gotowe do zapisu zmiany oraz listę zastrzeżeń. Nic nie zapisuje —
    decyzję podejmujesz po obejrzeniu.
    """
    wg_id = {int(d["id"]): d for d in partia}
    dane = _wyodrebnij_tablice(odpowiedz)

    zmiany, zastrzezenia = [], []
    widziane = set()

    for pozycja in dane:
        if not isinstance(pozycja, dict):
            continue
        try:
            ident = int(pozycja.get("id"))
        except (TypeError, ValueError):
            zastrzezenia.append(f"Pozycja bez poprawnego id: {str(pozycja)[:80]}")
            continue

        rekord = wg_id.get(ident)
        if rekord is None:
            # Model bywa uczynny i dorzuca dokumenty spoza partii. To znak,
            # że coś poszło nie tak — nie zapisujemy niczego, czego nie
            # wysłaliśmy.
            zastrzezenia.append(f"id {ident} nie należy do tej partii — pomijam.")
            continue
        if ident in widziane:
            zastrzezenia.append(f"id {ident} powtórzone — biorę pierwsze wystąpienie.")
            continue
        widziane.add(ident)

        nowy_przedmiot = ""
        if not rekord["przedmiot"]:
            trafione = _waliduj_przedmioty(pozycja.get("przedmiot"), podatek)
            if trafione:
                nowy_przedmiot = trafione[0]
            else:
                zastrzezenia.append(
                    f'{rekord["sygnatura"]}: przedmiot spoza taksonomii '
                    f'({str(pozycja.get("przedmiot"))[:60]}) — pomijam.')

        nowe_branze = ""
        if not rekord["branze"]:
            trafione = _waliduj_branze(pozycja.get("branze"))
            if trafione:
                nowe_branze = ", ".join(trafione)
            else:
                zastrzezenia.append(
                    f'{rekord["sygnatura"]}: branże spoza taksonomii '
                    f'({str(pozycja.get("branze"))[:60]}) — pomijam.')

        if nowy_przedmiot or nowe_branze:
            zmiany.append({
                "id": ident,
                "sygnatura": rekord["sygnatura"],
                "przedmiot": nowy_przedmiot,
                "branze": nowe_branze,
            })

    brakujace = [d["sygnatura"] for i, d in wg_id.items() if i not in widziane]
    if brakujace:
        zastrzezenia.append(
            f"Model pominął {len(brakujace)} dokumentów: "
            + ", ".join(brakujace[:5])
            + (" …" if len(brakujace) > 5 else "")
            + ". Zostaną w kolejce do następnej partii.")

    return {"zmiany": zmiany, "zastrzezenia": zastrzezenia,
            "rozpoznano": len(dane)}


def _zapisz(zmiany: list[dict]) -> int:
    """Zapis wyłącznie do pustych kolumn.

    Warunek `COALESCE(...) = ''` w SQL, a nie tylko w Pythonie: między
    pobraniem partii a zapisem mogła wejść ręczna klasyfikacja i nie chcemy
    jej nadpisać.
    """
    db = _db()
    zapisane = 0

    for z in zmiany:
        ustaw, parametry = [], []
        if z["przedmiot"]:
            ustaw.append("przedmiot = %s")
            parametry.append(z["przedmiot"])
        if z["branze"]:
            ustaw.append("branze = %s")
            parametry.append(z["branze"])
        if not ustaw:
            continue

        warunki = []
        if z["przedmiot"]:
            warunki.append("COALESCE(przedmiot, '') = ''")
        if z["branze"]:
            warunki.append("COALESCE(branze, '') = ''")

        parametry.append(z["id"])
        db.wykonaj(
            f"UPDATE streszczenia_auto SET {', '.join(ustaw)} "
            f"WHERE id = %s AND ({' OR '.join(warunki)})",
            tuple(parametry),
        )
        zapisane += 1

    return zapisane


# ---------------------------------------------------------------------------
# INTERFEJS
# ---------------------------------------------------------------------------

def pokaz_uzupelnianie() -> None:
    k = paleta.kolory() if hasattr(paleta, "kolory") else paleta.JASNY

    st.header("🧩 Uzupełnianie klasyfikacji")
    st.caption(
        "Domyka brakujące przedmioty i branże w streszczeniach. Polecenie "
        "składasz tutaj, przez model przepuszczasz sam, odpowiedź wklejasz "
        "z powrotem. Taksonomia jest dołączana do każdej partii."
    )

    zaladuj_taksonomie()
    st.caption(f"Taksonomia: {zrodlo_taksonomii()} — "
               f"{len(branze())} branż.")

    # ── stan braków ──
    if st.button("Policz braki", type="primary"):
        st.session_state["uk_stat"] = _statystyki()

    stat = st.session_state.get("uk_stat")
    if stat:
        wiersze = [{
            "Podatek": r["podatek"],
            "Bez przedmiotu": r["bez_przedmiotu"],
            "Bez branży": r["bez_branzy"],
            "Bez obu": r["bez_obu"],
            "Streszczeń razem": r["wszystkich"],
        } for r in stat if r["bez_przedmiotu"] or r["bez_branzy"]]

        if not wiersze:
            st.success("Nie ma braków — wszystkie streszczenia są sklasyfikowane.")
            return
        st.dataframe(wiersze, hide_index=True, use_container_width=True)

    st.divider()

    # ── partia ──
    dostepne = [r["podatek"] for r in (stat or [])
                if r["bez_przedmiotu"] or r["bez_branzy"]]
    if not dostepne:
        st.info("Zacznij od przycisku „Policz braki”.")
        return

    kol1, kol2 = st.columns([2, 1])
    podatek = kol1.selectbox("Podatek", dostepne,
                             help="Jedna partia obejmuje jeden podatek — "
                                  "przedmioty mają osobną listę dla każdego.")
    ile = kol2.number_input("Dokumentów w partii", 1, MAKS_PARTIA,
                            DOMYSLNA_PARTIA)

    if st.button("Przygotuj partię"):
        partia = _partia(podatek, int(ile))
        if not partia:
            st.warning(f"Dla {podatek} nie ma już dokumentów bez klasyfikacji.")
            st.session_state.pop("uk_partia", None)
        else:
            st.session_state["uk_partia"] = partia
            st.session_state["uk_podatek"] = podatek
            st.session_state.pop("uk_wynik", None)

    partia = st.session_state.get("uk_partia")
    if not partia:
        return

    podatek_partii = st.session_state["uk_podatek"]
    polecenie = _polecenie(podatek_partii, partia)

    st.markdown(f"**Partia: {len(partia)} dokumentów, podatek "
                f"{podatek_partii}.** Skopiuj polecenie do swojego modelu, "
                f"a odpowiedź wklej niżej.")
    st.code(polecenie, language="text")

    odpowiedz = st.text_area("Odpowiedź modelu (tablica JSON)", height=200,
                             key="uk_odpowiedz")

    if st.button("Sprawdź odpowiedź"):
        if not odpowiedz.strip():
            st.warning("Pole jest puste.")
        else:
            st.session_state["uk_wynik"] = _sprawdz(
                odpowiedz, partia, podatek_partii)

    wynik = st.session_state.get("uk_wynik")
    if not wynik:
        return

    for uwaga in wynik["zastrzezenia"]:
        st.warning(uwaga)

    if not wynik["zmiany"]:
        st.error("Nie ma nic do zapisania. Sprawdź, czy model zwrócił samą "
                 "tablicę JSON i czy użył wartości z podanych list.")
        return

    st.success(f"Do zapisania: {len(wynik['zmiany'])} dokumentów.")
    st.dataframe(
        [{"Sygnatura": z["sygnatura"],
          "Przedmiot": z["przedmiot"] or "— (już był)",
          "Branże": z["branze"] or "— (już były)"} for z in wynik["zmiany"]],
        hide_index=True, use_container_width=True)

    if st.button("Zapisz do bazy", type="primary"):
        zapisane = _zapisz(wynik["zmiany"])
        st.success(f"Zapisano {zapisane} dokumentów.")
        # Statystyki i partia są już nieaktualne — kasujemy, żeby nie sugerować
        # stanu sprzed zapisu.
        for klucz in ("uk_partia", "uk_wynik", "uk_stat"):
            st.session_state.pop(klucz, None)
        st.rerun()
