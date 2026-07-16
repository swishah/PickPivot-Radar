# -*- coding: utf-8 -*-
"""
MODUŁ 7: Monitoring Fraz — Skaner Doradca (UI).
Dodawanie i zarządzanie obserwowanymi frazami. Gdy w nowej interpretacji
(pobranej przez synchronizację) pojawi się obserwowana fraza, na wskazany
adres e-mail trafia zbiorcze powiadomienie (wysyła je skrypt monitoring_fraz.py
w GitHub Actions; ten moduł tylko zarządza frazami i pokazuje historię).

WSKAZÓWKA dot. fraz: dopasowanie działa na zasadzie „fragment tekstu”
(ILIKE, bez rozróżniania wielkości liter). Dla polskich odmian wpisuj rdzeń:
„ciepłownictw” złapie „ciepłownictwa”, „ciepłownictwem”, „ciepłownictwie”.
"""

from __future__ import annotations

import datetime as dt
import re

import streamlit as st

import archiwum_supabase
import monitoring_fraz as mfr

PODATKI_OPCJE = ["(wszystkie)", "PIT", "CIT", "VAT", "AKCYZA"]


def _zapytaj(sql: str, p: tuple | None = None) -> list[dict]:
    return archiwum_supabase._get_db().wykonaj(sql, p, fetch=True)


def _wykonaj(sql: str, p: tuple | None = None) -> int:
    return archiwum_supabase._get_db().wykonaj(sql, p, fetch=False)


@st.cache_resource(show_spinner=False)
def _zapewnij_tabele() -> bool:
    mfr.zapewnij_tabele(archiwum_supabase._get_db())
    return True


def _email_poprawny(e: str) -> bool:
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$", e or ""))


# ---------------------------------------------------------------------------
def pokaz_monitoring_fraz() -> None:
    st.header("🔔 Monitoring Fraz")
    st.caption(
        "Obserwuj frazy w nowych interpretacjach. Gdy synchronizacja pobierze "
        "interpretację zawierającą frazę, na wskazany e-mail trafi zbiorcze "
        "powiadomienie (sprawdzanie odbywa się automatycznie po synchronizacji)."
    )

    try:
        _zapewnij_tabele()
    except Exception as e:
        st.error(f"Nie udało się przygotować tabel: {e}")
        return

    # ── DODAWANIE ───────────────────────────────────────────────────────────
    st.subheader("Dodaj obserwowaną frazę")
    c1, c2, c3 = st.columns([2, 2, 1])
    with c1:
        fraza = st.text_input(
            "Fraza", key="mf_fraza",
            help="Fragment tekstu, bez wielkości liter. Dla odmian wpisz rdzeń: "
                 "„ciepłownictw” złapie „ciepłownictwa/-em/-ie”.",
        )
    with c2:
        email = st.text_input("E-mail do powiadomień", key="mf_email")
    with c3:
        podatek = st.selectbox("Podatek", PODATKI_OPCJE, key="mf_podatek")

    if st.button("➕ Dodaj", type="primary", key="mf_dodaj"):
        f = (fraza or "").strip()
        e = (email or "").strip().lower()
        p = "" if podatek == "(wszystkie)" else podatek
        if len(f) < 4:
            st.error("Fraza musi mieć co najmniej 4 znaki (krótsze dają lawinę "
                     "przypadkowych trafień).")
        elif not _email_poprawny(e):
            st.error("Podaj poprawny adres e-mail.")
        else:
            try:
                _wykonaj(
                    """INSERT INTO obserwowane_frazy
                         (fraza, email, podatek, aktywna, utworzono)
                       VALUES (%s,%s,%s,TRUE,%s)
                       ON CONFLICT (fraza, email, podatek)
                       DO UPDATE SET aktywna = TRUE""",
                    (f, e, p, dt.datetime.now().isoformat(timespec="seconds")),
                )
                st.success(f"Obserwuję frazę „{f}” ({podatek}) → {e}")
                st.rerun()
            except Exception as ex:
                st.error(f"Nie udało się dodać: {ex}")

    st.divider()

    # ── LISTA AKTYWNYCH ─────────────────────────────────────────────────────
    st.subheader("Obserwowane frazy")
    frazy = _zapytaj(
        "SELECT id, fraza, email, podatek, utworzono FROM obserwowane_frazy "
        "WHERE aktywna = TRUE ORDER BY email, fraza"
    )
    if not frazy:
        st.info("Brak aktywnych fraz. Dodaj pierwszą powyżej.")
    else:
        for f in frazy:
            c1, c2, c3, c4 = st.columns([3, 3, 1, 1])
            c1.markdown(f"**„{f['fraza']}”**")
            c2.markdown(f"{f['email']}")
            c3.markdown(f"`{f['podatek'] or 'wszystkie'}`")
            if c4.button("🗑️", key=f"mf_usun_{f['id']}",
                         help="Przestań obserwować"):
                _wykonaj("UPDATE obserwowane_frazy SET aktywna=FALSE WHERE id=%s",
                         (f["id"],))
                st.rerun()

    # ── HISTORIA POWIADOMIEŃ ────────────────────────────────────────────────
    with st.expander("Historia wysłanych powiadomień (ostatnie 30)"):
        hist = _zapytaj(
            """SELECT w.wyslano, f.fraza, f.email, w.dokument_id
               FROM monitoring_wyslane w
               JOIN obserwowane_frazy f ON f.id = w.fraza_id
               ORDER BY w.wyslano DESC LIMIT 30"""
        )
        if not hist:
            st.caption("Jeszcze nic nie wysłano.")
        for h in hist:
            st.caption(f"{str(h['wyslano'])[:16].replace('T',' ')} — "
                       f"„{h['fraza']}” → {h['email']} (dok. {h['dokument_id']})")


if __name__ == "__main__":
    st.set_page_config(page_title="Monitoring Fraz", layout="wide")
    pokaz_monitoring_fraz()
