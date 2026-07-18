# -*- coding: utf-8 -*-
"""
MODUŁ 9: Monitoring Przedmiotów — Skaner Doradca (UI).
Subskrypcja powiadomień e-mail o interpretacjach z wybranego OBSZARU
MERYTORYCZNEGO (przedmiotu), np. „estoński CIT", „podatek u źródła (WHT)",
„fakturowanie i KSeF". Przedmiot nadaje model językowy przy streszczaniu,
wybierając z zamkniętej taksonomii właściwej dla podatku interpretacji.

Powiadomienia wysyła skrypt monitoring_fraz.py (GitHub Actions — wspólny
harmonogram i SMTP z monitoringiem fraz i branż).
"""

from __future__ import annotations

import datetime as dt
import re

import streamlit as st

import archiwum_supabase
import monitoring_fraz as mfr
from streszczacz_openrouter import PRZEDMIOTY

PODATKI = list(PRZEDMIOTY.keys())  # CIT / VAT / PIT / AKCYZA


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


def pokaz_monitoring_przedmiotow() -> None:
    st.header("📚 Monitoring Przedmiotów")
    st.caption(
        "Obserwuj obszary merytoryczne (np. estoński CIT, WHT, KSeF). "
        "Obszar nadaje model przy streszczaniu, oceniając treść interpretacji. "
        "Nowe interpretacje z obserwowanego obszaru przyjdą mailem."
    )

    try:
        _zapewnij_tabele()
    except Exception as e:
        st.error(f"Nie udało się przygotować tabel: {e}")
        return

    # ── DODAWANIE SUBSKRYPCJI ───────────────────────────────────────────────
    st.subheader("Zasubskrybuj obszar")
    c1, c2, c3 = st.columns([1, 3, 2])
    with c1:
        podatek = st.selectbox("Podatek", PODATKI, key="mp_podatek")
    with c2:
        przedmiot = st.selectbox(
            "Przedmiot (obszar merytoryczny)",
            PRZEDMIOTY[podatek],
            key=f"mp_przedmiot_{podatek}",
        )
    with c3:
        email = st.text_input("E-mail do powiadomień", key="mp_email")

    if st.button("➕ Subskrybuj", type="primary", key="mp_dodaj"):
        e = (email or "").strip().lower()
        if not _email_poprawny(e):
            st.error("Podaj poprawny adres e-mail.")
        else:
            try:
                _wykonaj(
                    """INSERT INTO obserwowane_przedmioty
                         (przedmiot, podatek, email, aktywna, utworzono)
                       VALUES (%s,%s,%s,TRUE,%s)
                       ON CONFLICT (przedmiot, email) DO UPDATE SET aktywna = TRUE""",
                    (przedmiot, podatek, e,
                     dt.datetime.now().isoformat(timespec="seconds")),
                )
                st.success(f"Subskrypcja: {przedmiot} ({podatek}) → {e}")
                st.rerun()
            except Exception as ex:
                st.error(f"Nie udało się dodać: {ex}")

    st.divider()

    # ── AKTYWNE SUBSKRYPCJE ─────────────────────────────────────────────────
    st.subheader("Aktywne subskrypcje")
    subs = _zapytaj(
        "SELECT id, przedmiot, podatek, email FROM obserwowane_przedmioty "
        "WHERE aktywna = TRUE ORDER BY email, podatek, przedmiot"
    )
    if not subs:
        st.info("Brak subskrypcji. Dodaj pierwszą powyżej.")
    else:
        for s in subs:
            c1, c2, c3, c4 = st.columns([3, 1, 3, 1])
            c1.markdown(f"**{s['przedmiot']}**")
            c2.markdown(f"`{s['podatek']}`")
            c3.markdown(f"{s['email']}")
            if c4.button("🗑️", key=f"mp_usun_{s['id']}", help="Anuluj subskrypcję"):
                _wykonaj("UPDATE obserwowane_przedmioty SET aktywna=FALSE WHERE id=%s",
                         (s["id"],))
                st.rerun()

    # ── ROZKŁAD PRZEDMIOTÓW ─────────────────────────────────────────────────
    with st.expander("Rozkład przedmiotów w dotychczasowych streszczeniach"):
        try:
            rozkl = _zapytaj(
                """SELECT TRIM(x.p) AS przedmiot, COUNT(*) AS n
                   FROM streszczenia_auto s,
                        LATERAL unnest(string_to_array(s.przedmiot, ';')) AS x(p)
                   WHERE COALESCE(s.przedmiot,'') <> ''
                   GROUP BY TRIM(x.p) ORDER BY n DESC"""
            )
            if not rozkl:
                st.caption(
                    "Brak sklasyfikowanych streszczeń — przedmiot nadawany jest "
                    "wyłącznie NOWYM streszczeniom (od wdrożenia tej funkcji)."
                )
            for r in rozkl:
                st.caption(f"{r['przedmiot']}: {r['n']}")
        except Exception as e:
            st.caption(f"Nie udało się policzyć rozkładu: {e}")

    # ── HISTORIA POWIADOMIEŃ ────────────────────────────────────────────────
    with st.expander("Historia wysłanych powiadomień (ostatnie 30)"):
        hist = _zapytaj(
            """SELECT w.wyslano, p.przedmiot, p.email, w.dokument_id
               FROM monitoring_przedmioty_wyslane w
               JOIN obserwowane_przedmioty p ON p.id = w.sub_id
               ORDER BY w.wyslano DESC LIMIT 30"""
        )
        if not hist:
            st.caption("Jeszcze nic nie wysłano.")
        for h in hist:
            st.caption(f"{str(h['wyslano'])[:16].replace('T',' ')} — "
                       f"{h['przedmiot']} → {h['email']} (dok. {h['dokument_id']})")


if __name__ == "__main__":
    st.set_page_config(page_title="Monitoring Przedmiotów", layout="wide")
    pokaz_monitoring_przedmiotow()
