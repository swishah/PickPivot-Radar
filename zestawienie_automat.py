# -*- coding: utf-8 -*-
"""
MODUŁ 6: Zestawienie Tygodniowe — AUTOMAT (wersja próbna)
===============================================================================
Równoległy do modułu 5. Zamiast wgrywać plik z GPT, streszcza interpretacje
WPROST Z BAZY przez OpenRouter (darmowe modele) i renderuje TĘ SAMĄ tabelę
(L.p. | Sygnatura | Data wydania | Temat | Streszczenie), z zielonym
oznaczeniem publikacji opóźnionych (identyczna zasada jak w module 5).

Cel: porównać jakość darmowego streszczania z dotychczasowym obiegiem DOCX.

Źródło danych: tabela `dokumenty` (ta sama, którą zasila synchronizacja
dzienna). Nowe interpretacje pojawiają się tu automatycznie — wystarczy je
streścić (przycisk „Streść brakujące”). Wyniki trafiają do `streszczenia_auto`
i nie są liczone ponownie (oszczędza darmowy limit).

WYMAGA: sekcji [openrouter] w Streamlit Secrets z kluczem api_key
oraz kolumny dokumenty.pobrano_at (migracja_pobrano_at.sql).
Renderer i logika tygodni pn–nd są współdzielone z modułem 5.
===============================================================================
"""

from __future__ import annotations

import datetime as dt
import time

import streamlit as st

import archiwum_supabase
import streszczacz_openrouter as sopen
from zestawienie_tygodniowe import _etykieta_tygodnia, _klucz_tygodnia, _tabela_html

PODATKI = ["PIT", "CIT", "VAT", "AKCYZA"]
MAKS_TYGODNI = 104
BATCH_MAKS = 15  # ile interpretacji streścić za jednym kliknięciem (limit darmowy)
PRZERWA_S = 3.5  # odstęp między zapytaniami (limit ~20/min darmowej puli)

# Automat streszcza WYŁĄCZNIE interpretacje wydane od tej daty włącznie
# (data_wyd >= DATA_START). Przeszłe interpretacje są pomijane — start „od teraz”,
# potem wszystko na bieżąco. Format YYYY-MM-DD; porównanie łańcuchowe jest
# poprawne, bo data_wyd jest przechowywana jako tekst ISO.
DATA_START = "2026-07-15"


# ---------------------------------------------------------------------------
# BAZA
# ---------------------------------------------------------------------------
def _zapytaj(sql: str, p: tuple | None = None) -> list[dict]:
    return archiwum_supabase._get_db().wykonaj(sql, p, fetch=True)


def _wykonaj(sql: str, p: tuple | None = None) -> int:
    return archiwum_supabase._get_db().wykonaj(sql, p, fetch=False)


@st.cache_resource(show_spinner=False)
def _zapewnij_tabele() -> bool:
    _wykonaj(
        """
        CREATE TABLE IF NOT EXISTS streszczenia_auto (
            id           SERIAL PRIMARY KEY,
            dokument_id  TEXT NOT NULL,
            podatek      TEXT NOT NULL,
            model        TEXT NOT NULL,
            temat        TEXT DEFAULT '',
            streszczenie TEXT DEFAULT '',
            wygenerowano TEXT NOT NULL,
            UNIQUE (dokument_id, model)
        )
        """
    )
    # Kolumna branż (klasyfikacja treściowa) — dokładana bezpiecznie do
    # istniejącej tabeli; starsze wpisy mają '' (bez branży).
    _wykonaj(
        "ALTER TABLE streszczenia_auto ADD COLUMN IF NOT EXISTS branze TEXT DEFAULT ''"
    )
    _wykonaj(
        "ALTER TABLE streszczenia_auto ADD COLUMN IF NOT EXISTS przedmiot TEXT DEFAULT ''"
    )
    return True


# ---------------------------------------------------------------------------
# TYGODNIE
# ---------------------------------------------------------------------------
def _poniedzialek(d: dt.date) -> dt.date:
    return d - dt.timedelta(days=d.weekday())


@st.cache_data(ttl=1800, show_spinner=False)
def _lista_tygodni(podatek: str) -> list[str]:
    dzis = dt.date.today()
    biezacy = _poniedzialek(dzis)
    # Nie schodzimy poniżej tygodnia progu DATA_START.
    najstarsza = _poniedzialek(dt.date.fromisoformat(DATA_START))
    try:
        w = _zapytaj(
            "SELECT MIN(NULLIF(data_wyd,'')) AS m FROM dokumenty "
            "WHERE podatek=%s AND data_wyd >= %s",
            (podatek, DATA_START),
        )
        if w and w[0].get("m"):
            najstarsza = _poniedzialek(dt.date.fromisoformat(str(w[0]["m"])[:10]))
    except Exception:
        pass
    klucze, pon = [], biezacy
    while pon >= najstarsza and len(klucze) < MAKS_TYGODNI:
        klucze.append(_klucz_tygodnia(pon))
        pon -= dt.timedelta(days=7)
    return klucze


def _granice(klucz: str) -> tuple[dt.date, dt.date]:
    rok, wk = klucz.split("-W")
    pon = dt.date.fromisocalendar(int(rok), int(wk), 1)
    return pon, pon + dt.timedelta(days=6)


# ---------------------------------------------------------------------------
# ODCZYT INTERPRETACJI + STRESZCZEŃ
# ---------------------------------------------------------------------------
def _sensowne(s: str | None) -> bool:
    """Czy zapisane streszczenie nadaje się do pokazania. Korzysta ze wspólnej
    kontroli z klienta OpenRouter (odrzuca puste, surowy/ucięty JSON, zbyt
    krótkie, etykiety bezpieczeństwa/odmowy oraz angielski). Wadliwe rekordy
    są traktowane jak brakujące — można je wygenerować ponownie przyciskiem."""
    return not sopen.streszczenie_wadliwe(s)


def _interpretacje(podatek: str, klucz: str, model: str) -> list[dict]:
    pon, nd = _granice(klucz)
    rows = _zapytaj(
        """
        SELECT d.id, d.sygnatura, d.data_wyd, d.tekst,
               s.temat AS s_temat, s.streszczenie AS s_streszcz,
               COALESCE(s.branze, '') AS s_branze,
               COALESCE(s.przedmiot, '') AS s_przedmiot
        FROM dokumenty d
        LEFT JOIN streszczenia_auto s
               ON s.dokument_id = d.id AND s.model = %s
        WHERE d.podatek = %s
          AND d.data_wyd >= %s
          AND ( (d.data_wyd >= %s AND d.data_wyd <= %s)
             OR (d.pobrano_at >= %s::date AND d.pobrano_at < (%s::date + 1)
                 AND d.data_wyd < %s) )
        ORDER BY d.data_wyd, d.sygnatura
        """,
        (model, podatek, DATA_START, pon.isoformat(), nd.isoformat(),
         pon.isoformat(), nd.isoformat(), pon.isoformat()),
    )
    for r in rows:
        r["pozny"] = str(r["data_wyd"]) < pon.isoformat()
        s = r.get("s_streszcz")
        r["_ma"] = _sensowne(s)
        r["temat"] = (r.get("s_temat") or "") if r["_ma"] else ""
        r["branza"] = (r.get("s_branze") or "") if r["_ma"] else ""
        r["przedmiot"] = (r.get("s_przedmiot") or "") if r["_ma"] else ""
        r["streszczenie"] = s if r["_ma"] else "— (brak streszczenia)"
    return rows


def _zapisz_streszczenie(dok_id: str, podatek: str, model: str,
                         temat: str, streszcz: str, branze: str = "",
                         przedmiot: str = "") -> None:
    _wykonaj(
        """
        INSERT INTO streszczenia_auto
            (dokument_id, podatek, model, temat, streszczenie, branze,
             przedmiot, wygenerowano)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (dokument_id, model) DO UPDATE SET
            temat=EXCLUDED.temat, streszczenie=EXCLUDED.streszczenie,
            branze=EXCLUDED.branze, przedmiot=EXCLUDED.przedmiot,
            wygenerowano=EXCLUDED.wygenerowano
        """,
        (dok_id, podatek, model, temat, streszcz, branze, przedmiot,
         dt.datetime.now().isoformat(timespec="seconds")),
    )


# ---------------------------------------------------------------------------
# KLUCZ API
# ---------------------------------------------------------------------------
def _api_key() -> str | None:
    try:
        return st.secrets["openrouter"]["api_key"]
    except Exception:
        try:
            return st.secrets["OPENROUTER_API_KEY"]
        except Exception:
            return None


# ---------------------------------------------------------------------------
# ZAKŁADKA
# ---------------------------------------------------------------------------
def _zakladka(podatek: str, model: str, klucz_api: str | None) -> None:
    tygodnie = _lista_tygodni(podatek)
    if not tygodnie:
        st.info("Brak interpretacji w bazie dla tego podatku.")
        return

    W = st.selectbox("Tydzień zestawienia", options=tygodnie,
                     format_func=_etykieta_tygodnia, key=f"auto_tydz_{podatek}")

    rekordy = _interpretacje(podatek, W, model)
    if not rekordy:
        st.info("Brak interpretacji przypisanych do tego tygodnia.")
        return

    brak = [r for r in rekordy if not r.get("_ma")]
    n_pozne = sum(1 for r in rekordy if r.get("pozny"))

    k1, k2, k3 = st.columns(3)
    k1.metric("Interpretacji w tygodniu", len(rekordy))
    k2.metric("Bez streszczenia", len(brak))
    k3.metric("Publikacje opóźnione", n_pozne)

    if brak:
        if not klucz_api:
            st.warning(
                "Brak klucza OpenRouter — dodaj sekcję [openrouter] w Secrets, "
                "aby streszczać (patrz instrukcja). Poniżej i tak zobaczysz tabelę."
            )
        else:
            do_zrobienia = min(len(brak), BATCH_MAKS)
            if st.button(
                f"🤖 Streść brakujące ({do_zrobienia} z {len(brak)}) — model: {model}",
                key=f"auto_btn_{podatek}_{W}", type="primary",
            ):
                _streszczaj(brak[:BATCH_MAKS], podatek, model, klucz_api)
                st.rerun()

    if n_pozne:
        st.caption(
            f"🟢 {n_pozne} pozycji na zielono to publikacje opóźnione — wydane "
            f"wcześniej, do bazy trafiły w tym tygodniu. W swoim właściwym "
            f"(wcześniejszym) tygodniu widnieją normalnie."
        )

    st.markdown(_tabela_html(rekordy), unsafe_allow_html=True)
    st.caption(
        f"Streszczenia generowane automatycznie modelem `{model}` (OpenRouter). "
        f"Wersja próbna — zawsze weryfikuj przed użyciem w doradztwie."
    )


def _streszczaj(pozycje: list[dict], podatek: str, model: str, klucz_api: str) -> None:
    pasek = st.progress(0.0, text="Streszczam…")
    ok, bledy = 0, 0
    for i, r in enumerate(pozycje, start=1):
        try:
            wynik = sopen.streszcz_tekst(
                r.get("tekst") or "", r["sygnatura"], str(r["data_wyd"]),
                api_key=klucz_api, model=model, podatek=podatek,
            )
            _zapisz_streszczenie(r["id"], podatek, model,
                                 wynik["temat"], wynik["streszczenie"],
                                 ", ".join(wynik.get("branze") or []),
                                 "; ".join(wynik.get("przedmioty") or []))
            ok += 1
        except Exception as e:
            bledy += 1
            st.warning(f"{r['sygnatura']}: {e}")
            if "401" in str(e) or "402" in str(e) or "403" in str(e):
                break  # problem z kluczem/kredytami — nie ma sensu kontynuować
        pasek.progress(i / len(pozycje), text=f"Streszczam… {i}/{len(pozycje)}")
        if i < len(pozycje):
            time.sleep(PRZERWA_S)  # szacunek dla limitu ~20/min
    pasek.empty()
    if ok:
        st.success(f"Zapisano {ok} streszczeń.")
    if bledy:
        st.info(f"Nie udało się: {bledy}. Spróbuj ponownie później "
                f"(limit dzienny/na minutę) lub zmień model.")


# ---------------------------------------------------------------------------
# WEJŚCIE
# ---------------------------------------------------------------------------
def pokaz_zestawienie_automat() -> None:
    st.header("🤖 Zestawienie tygodniowe — Automat (wersja próbna)")
    st.caption(
        "Streszczenia generowane wprost z bazy przez OpenRouter (darmowe modele). "
        "Ta sama tabela co w module 5 — do porównania jakości z obiegiem DOCX."
    )
    st.caption(
        f"Zakres: interpretacje wydane od **{dt.date.fromisoformat(DATA_START):%d.%m.%Y}** "
        f"włącznie (wcześniejsze są pomijane)."
    )

    try:
        _zapewnij_tabele()
    except Exception as e:
        st.error(f"Nie udało się przygotować tabeli streszczeń: {e}")
        return

    klucz_api = _api_key()

    c1, c2 = st.columns([2, 3])
    with c1:
        model = st.selectbox("Model (OpenRouter)", options=sopen.MODELE_DO_WYBORU,
                             index=0, key="auto_model")
    with c2:
        st.caption(
            "Domyślnie `openrouter/free` (auto-router darmowych modeli — odporny "
            "na rotację oferty). Limit darmowy: ~20 zapytań/min, 50/dobę "
            "(≥10 kredytów podnosi do ~1000/dobę)."
        )

    for zakladka_ui, podatek in zip(st.tabs(PODATKI), PODATKI):
        with zakladka_ui:
            _zakladka(podatek, model, klucz_api)


if __name__ == "__main__":
    st.set_page_config(page_title="Zestawienie automat", layout="wide")
    pokaz_zestawienie_automat()
