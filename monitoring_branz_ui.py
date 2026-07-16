# -*- coding: utf-8 -*-
"""
MODUŁ 8: Monitoring Branż — Skaner Doradca (UI).
Subskrypcja powiadomień e-mail o interpretacjach z wybranej branży.
Branżę nadaje model językowy PODCZAS STRESZCZANIA (moduł 6 / automat),
oceniając TREŚĆ interpretacji (czym zajmuje się wnioskodawca) — to nie jest
wyszukiwanie słów kluczowych. Model wybiera z zamkniętej taksonomii, dzięki
czemu subskrypcje trafiają deterministycznie.

Powiadomienia wysyła skrypt monitoring_fraz.py (GitHub Actions, wspólny
harmonogram i SMTP z monitoringiem fraz).
"""

from __future__ import annotations

import datetime as dt
import re

import streamlit as st

import archiwum_supabase
import monitoring_fraz as mfr
from streszczacz_openrouter import BRANZE


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


def pokaz_monitoring_branz() -> None:
    st.header("🏭 Monitoring Branż")
    st.caption(
        "Model językowy przy streszczaniu ocenia PO TREŚCI, jakiej branży "
        "dotyczy interpretacja (nie po słowach kluczowych). Wybierz branżę "
        "i adres e-mail — nowe interpretacje z tej branży przyjdą mailem."
    )

    try:
        _zapewnij_tabele()
    except Exception as e:
        st.error(f"Nie udało się przygotować tabel: {e}")
        return

    # ── DODAWANIE SUBSKRYPCJI ───────────────────────────────────────────────
    st.subheader("Zasubskrybuj branżę")
    c1, c2 = st.columns([2, 2])
    with c1:
        branza = st.selectbox("Branża", BRANZE, key="mb_branza")
    with c2:
        email = st.text_input("E-mail do powiadomień", key="mb_email")

    if st.button("➕ Subskrybuj", type="primary", key="mb_dodaj"):
        e = (email or "").strip().lower()
        if not _email_poprawny(e):
            st.error("Podaj poprawny adres e-mail.")
        else:
            try:
                _wykonaj(
                    """INSERT INTO obserwowane_branze
                         (branza, email, aktywna, utworzono)
                       VALUES (%s,%s,TRUE,%s)
                       ON CONFLICT (branza, email) DO UPDATE SET aktywna = TRUE""",
                    (branza, e, dt.datetime.now().isoformat(timespec="seconds")),
                )
                st.success(f"Subskrypcja: branża {branza} → {e}")
                st.rerun()
            except Exception as ex:
                st.error(f"Nie udało się dodać: {ex}")

    st.divider()

    # ── AKTYWNE SUBSKRYPCJE ─────────────────────────────────────────────────
    st.subheader("Aktywne subskrypcje")
    subs = _zapytaj(
        "SELECT id, branza, email, utworzono FROM obserwowane_branze "
        "WHERE aktywna = TRUE ORDER BY email, branza"
    )
    if not subs:
        st.info("Brak subskrypcji. Dodaj pierwszą powyżej.")
    else:
        for s in subs:
            c1, c2, c3 = st.columns([3, 3, 1])
            c1.markdown(f"**{s['branza']}**")
            c2.markdown(f"{s['email']}")
            if c3.button("🗑️", key=f"mb_usun_{s['id']}", help="Anuluj subskrypcję"):
                _wykonaj("UPDATE obserwowane_branze SET aktywna=FALSE WHERE id=%s",
                         (s["id"],))
                st.rerun()

    # ── STATYSTYKA KLASYFIKACJI ─────────────────────────────────────────────
    with st.expander("Rozkład branż w dotychczasowych streszczeniach"):
        try:
            rozkl = _zapytaj(
                """SELECT TRIM(x.b) AS branza, COUNT(*) AS n
                   FROM streszczenia_auto s,
                        LATERAL unnest(string_to_array(s.branze, ',')) AS x(b)
                   WHERE COALESCE(s.branze,'') <> ''
                   GROUP BY TRIM(x.b) ORDER BY n DESC"""
            )
            if not rozkl:
                st.caption(
                    "Brak sklasyfikowanych streszczeń. Branże nadawane są NOWYM "
                    "streszczeniom; starsze można doklasyfikować (patrz opis modułu)."
                )
            for r in rozkl:
                st.caption(f"{r['branza']}: {r['n']}")
        except Exception as e:
            st.caption(f"Nie udało się policzyć rozkładu: {e}")

    # ── HISTORIA POWIADOMIEŃ ────────────────────────────────────────────────
    with st.expander("Historia wysłanych powiadomień (ostatnie 30)"):
        hist = _zapytaj(
            """SELECT w.wyslano, b.branza, b.email, w.dokument_id
               FROM monitoring_branze_wyslane w
               JOIN obserwowane_branze b ON b.id = w.sub_id
               ORDER BY w.wyslano DESC LIMIT 30"""
        )
        if not hist:
            st.caption("Jeszcze nic nie wysłano.")
        for h in hist:
            st.caption(f"{str(h['wyslano'])[:16].replace('T',' ')} — "
                       f"{h['branza']} → {h['email']} (dok. {h['dokument_id']})")


if __name__ == "__main__":
    st.set_page_config(page_title="Monitoring Branż", layout="wide")
    pokaz_monitoring_branz()
