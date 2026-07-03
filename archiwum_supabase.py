"""
archiwum_supabase.py — Warstwa Streamlit nad db_core.py.

Sekcja Secrets w Streamlit:
[supabase]
host     = "aws-1-eu-central-1.pooler.supabase.com"
port     = "5432"
database = "postgres"
user     = "postgres.elidggvexfttbilxkwry"
password = "TwojeHaslo"
"""

import streamlit as st
import db_core


# ---------------------------------------------------------------------------
# POLACZENIE — cachowane jako obiekt SupabaseDB (sam obiekt jest lekki,
# nowe polaczenie TCP tworzone jest dopiero przy kazdym .wykonaj())
# ---------------------------------------------------------------------------
@st.cache_resource(show_spinner=False)
def _get_db() -> db_core.SupabaseDB:
    s = dict(st.secrets["supabase"])
    db = db_core.SupabaseDB(s)
    try:
        db.inicjalizuj_schemat()
    except Exception as e:
        st.error(f"Blad inicjalizacji bazy Supabase: {e}")
    return db


# ---------------------------------------------------------------------------
# ZAPIS
# ---------------------------------------------------------------------------
def zapisz_wiele_do_archiwum(rekordy: list, pobrano_kto: str = "system") -> int:
    if not rekordy:
        return 0
    db = _get_db()
    try:
        n = db_core.zapisz_wiele_do_archiwum(db, rekordy, pobrano_kto)
        pobierz_id_z_archiwum.clear()
        return n
    except Exception as e:
        st.warning(f"Blad zapisu: {e}")
        return 0


# ---------------------------------------------------------------------------
# KOMBINACJE
# ---------------------------------------------------------------------------
def oznacz_kombinacje(podatek: str, rok: int, miesiac: int):
    db = _get_db()
    try:
        db_core.oznacz_kombinacje(db, podatek, rok, miesiac)
        pobierz_ukonczone_kombinacje.clear()
    except Exception as e:
        st.warning(f"Blad oznaczania: {e}")


@st.cache_data(ttl=120, show_spinner=False)
def pobierz_ukonczone_kombinacje() -> set:
    db = _get_db()
    try:
        return db_core.pobierz_ukonczone_kombinacje(db)
    except Exception:
        return set()


# ---------------------------------------------------------------------------
# ODCZYT
# ---------------------------------------------------------------------------
@st.cache_data(ttl=60, show_spinner=False)
def pobierz_id_z_archiwum() -> set:
    db = _get_db()
    try:
        return db_core.pobierz_id_z_archiwum(db)
    except Exception:
        return set()


def pobierz_rekordy_z_archiwum(podatek=None, rok=None, miesiac=None) -> list:
    db = _get_db()
    try:
        return db_core.pobierz_rekordy_z_archiwum(db, podatek=podatek, rok=rok, miesiac=miesiac)
    except Exception as e:
        st.warning(f"Blad odczytu: {e}")
        return []


# ---------------------------------------------------------------------------
# STATYSTYKI
# ---------------------------------------------------------------------------
def statystyki_archiwum() -> dict:
    db = _get_db()
    try:
        return db_core.statystyki_archiwum(db)
    except Exception:
        return {"total": 0, "per_podatek": {}, "ostatnie_pobranie": "—",
                "ukonczone_kombinacje": 0, "polaczenie": False}


def statystyki_szczegolowe() -> dict:
    db = _get_db()
    try:
        return db_core.statystyki_szczegolowe(db)
    except Exception as e:
        st.warning(f"Blad pobierania statystyk: {e}")
        return {"total": 0, "per_podatek": []}


def rozklad_miesieczny(podatek: str = None) -> list:
    db = _get_db()
    try:
        return db_core.rozklad_miesieczny(db, podatek=podatek)
    except Exception as e:
        st.warning(f"Blad pobierania rozkladu miesiecznego: {e}")
        return []


# ---------------------------------------------------------------------------
# RAPORTY TYGODNIOWE (Modul 5)
# ---------------------------------------------------------------------------
def pobierz_liste_raportow() -> list:
    db = _get_db()
    try:
        return db_core.pobierz_liste_raportow(db)
    except Exception as e:
        st.warning(f"Blad odczytu listy raportow: {e}")
        return []


def pobierz_plik_raportu(raport_id: int):
    db = _get_db()
    try:
        return db_core.pobierz_plik_raportu(db, raport_id)
    except Exception as e:
        st.warning(f"Blad pobierania pliku: {e}")
        return None, None


# ---------------------------------------------------------------------------
# HISTORIA RAPORTOW NA ZADANIE (status + weryfikacja)
# ---------------------------------------------------------------------------
def pobierz_historie_raportow(limit: int = 30) -> list:
    db = _get_db()
    try:
        return db_core.pobierz_historie_raportow(db, limit=limit)
    except Exception as e:
        st.warning(f"Blad odczytu historii: {e}")
        return []


# ---------------------------------------------------------------------------
# HISTORIA SYNCHRONIZACJI DZIENNEJ (automatyczny job o 3:00)
# ---------------------------------------------------------------------------
def pobierz_historie_synchronizacji(limit: int = 30) -> list:
    db = _get_db()
    try:
        return db_core.pobierz_historie_synchronizacji(db, limit=limit)
    except Exception as e:
        st.warning(f"Blad odczytu historii synchronizacji: {e}")
        return []


# ---------------------------------------------------------------------------
# POMOCNICZE (reeksport z db_core dla wstecznej kompatybilnosci)
# ---------------------------------------------------------------------------
def _klucz_kombinacji(podatek, rok, miesiac):
    return db_core.klucz_kombinacji(podatek, rok, miesiac)

def _id_z_rekordu(r):
    return db_core._id_z_rekordu(r)
