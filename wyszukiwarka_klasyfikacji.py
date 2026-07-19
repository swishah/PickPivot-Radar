# -*- coding: utf-8 -*-
"""
MODUŁ 8: Wyszukiwarka Interpretacji — Skaner Doradca.
Przeszukiwanie sklasyfikowanych streszczeń po BRANŻY (kto pyta) i PRZEDMIOCIE
(czego dotyczy merytorycznie), z filtrem podatku, zakresu dat wydania oraz
opcjonalną frazą w temacie/streszczeniu.

Logika filtrów: w ramach jednego filtra wybory łączą się jako LUB
(np. branża ciepłownicza LUB energetyczna), między filtrami jako I
(branża × przedmiot × daty × fraza). Przykład użycia: „estoński CIT
w branży produkcyjnej z ostatnich 6 miesięcy”.

Źródło: streszczenia_auto JOIN dokumenty — czyli wyłącznie interpretacje,
które przeszły przez streszczanie z klasyfikacją (moduł 6 / automat).
"""

from __future__ import annotations

import datetime as dt

import streamlit as st

import archiwum_supabase
from streszczacz_openrouter import BRANZE, PRZEDMIOTY
from zestawienie_tygodniowe import _tabela_html

PODATKI = ["(wszystkie)"] + list(PRZEDMIOTY.keys())
LIMIT_WYNIKOW = 200


def _zapytaj(sql: str, p: tuple | None = None) -> list[dict]:
    return archiwum_supabase._get_db().wykonaj(sql, p, fetch=True)


# ---------------------------------------------------------------------------
def _przedmioty_dla(podatek: str) -> list[str]:
    """Lista przedmiotów do selektora: dla konkretnego podatku — jego
    taksonomia; dla „(wszystkie)” — suma wszystkich list."""
    if podatek in PRZEDMIOTY:
        return PRZEDMIOTY[podatek]
    wszystkie: list[str] = []
    for lista in PRZEDMIOTY.values():
        for p in lista:
            if p not in wszystkie:
                wszystkie.append(p)
    return wszystkie


def _szukaj(podatek: str, branze: list[str], przedmioty: list[str],
            data_od: dt.date | None, data_do: dt.date | None,
            fraza: str) -> list[dict]:
    warunki, parametry = [], []

    if podatek in PRZEDMIOTY:
        warunki.append("d.podatek = %s")
        parametry.append(podatek)

    if branze:
        czlony = []
        for b in branze:
            czlony.append("s.branze ILIKE %s")
            parametry.append(f"%{b}%")
        warunki.append("(" + " OR ".join(czlony) + ")")

    if przedmioty:
        czlony = []
        for p in przedmioty:
            czlony.append("s.przedmiot ILIKE %s")
            parametry.append(f"%{p}%")
        warunki.append("(" + " OR ".join(czlony) + ")")

    if data_od:
        warunki.append("d.data_wyd >= %s")
        parametry.append(data_od.isoformat())
    if data_do:
        warunki.append("d.data_wyd <= %s")
        parametry.append(data_do.isoformat())

    f = (fraza or "").strip()
    if f:
        warunki.append("(s.temat ILIKE %s OR s.streszczenie ILIKE %s)")
        parametry.extend([f"%{f}%", f"%{f}%"])

    where = ("WHERE " + " AND ".join(warunki)) if warunki else ""
    sql = f"""
        SELECT d.sygnatura, d.data_wyd, d.podatek, d.link,
               s.temat, s.streszczenie,
               COALESCE(s.branze, '')    AS branza,
               COALESCE(s.przedmiot, '') AS przedmiot
        FROM streszczenia_auto s
        JOIN dokumenty d ON d.id = s.dokument_id
        {where}
        ORDER BY d.data_wyd DESC, d.sygnatura
        LIMIT {LIMIT_WYNIKOW + 1}
    """
    return _zapytaj(sql, tuple(parametry) if parametry else None)


# ---------------------------------------------------------------------------
def pokaz_wyszukiwarke() -> None:
    st.header("🔎 Wyszukiwarka Interpretacji")
    st.caption(
        "Przeszukuj sklasyfikowane streszczenia po branży i przedmiocie "
        "(obszarze merytorycznym). W ramach filtra wybory łączą się jako "
        "LUB, między filtrami jako I."
    )

    c1, c2 = st.columns([1, 3])
    with c1:
        podatek = st.selectbox("Podatek", PODATKI, key="wk_podatek")
    with c2:
        fraza = st.text_input(
            "Fraza w temacie lub streszczeniu (opcjonalnie)", key="wk_fraza"
        )

    c3, c4 = st.columns(2)
    with c3:
        branze = st.multiselect("Branża (LUB)", BRANZE, key="wk_branze")
    with c4:
        przedmioty = st.multiselect(
            "Przedmiot / obszar merytoryczny (LUB)",
            _przedmioty_dla(podatek),
            key=f"wk_przedmioty_{podatek}",
        )

    c5, c6 = st.columns(2)
    with c5:
        uzyj_od = st.date_input(
            "Data wydania od", value=None, key="wk_od",
            format="DD.MM.YYYY",
        )
    with c6:
        uzyj_do = st.date_input(
            "Data wydania do", value=None, key="wk_do",
            format="DD.MM.YYYY",
        )

    if st.button("🔎 Szukaj", type="primary", key="wk_szukaj"):
        st.session_state["wk_uruchomiono"] = True

    if not st.session_state.get("wk_uruchomiono"):
        return

    try:
        wyniki = _szukaj(podatek, branze, przedmioty, uzyj_od, uzyj_do, fraza)
    except Exception as e:
        st.error(f"Błąd wyszukiwania: {e}")
        return

    obciete = len(wyniki) > LIMIT_WYNIKOW
    wyniki = wyniki[:LIMIT_WYNIKOW]

    if not wyniki:
        st.info(
            "Brak wyników. Pamiętaj: wyszukiwarka obejmuje wyłącznie "
            "interpretacje ze streszczeniem z klasyfikacją (nadawaną nowym "
            "streszczeniom od wdrożenia branż/przedmiotów)."
        )
        return

    st.caption(
        f"Znaleziono: **{len(wyniki)}**"
        + (f" (pokazuję pierwsze {LIMIT_WYNIKOW} — zawęź filtry)" if obciete else "")
    )

    for r in wyniki:
        r["pozny"] = False  # zielone oznaczenie nie dotyczy widoku wyszukiwarki

    st.markdown(_tabela_html(wyniki), unsafe_allow_html=True)

    with st.expander("Linki do interpretacji (Eureka)"):
        for r in wyniki:
            if r.get("link"):
                st.caption(f"[{r['podatek']}] {r['sygnatura']} — {r['link']}")


if __name__ == "__main__":
    st.set_page_config(page_title="Wyszukiwarka Interpretacji", layout="wide")
    pokaz_wyszukiwarke()
