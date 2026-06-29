"""
archiwum_supabase.py — Trwałe archiwum interpretacji podatkowych w PostgreSQL (Supabase).

Identyczny interfejs publiczny jak archiwum_drive.py — zamień import w downloader.py:
    import archiwum_supabase as archiwum

Schemat bazy tworzy się automatycznie przy pierwszym uruchomieniu.
Darmowy plan Supabase: 500 MB / ~50 000 wierszy — wystarczy na lata.
"""

import streamlit as st
import psycopg2
import psycopg2.extras
import threading
import hashlib
from datetime import datetime

# ---------------------------------------------------------------------------
# POŁĄCZENIE
# ---------------------------------------------------------------------------
_db_lock = threading.Lock()

@st.cache_resource(show_spinner=False)
def _polaczenie():
    """
    Tworzy i cachuje połączenie z Supabase PostgreSQL.
    Czyta URL z st.secrets["supabase"]["url"].
    """
    try:
        url  = st.secrets["supabase"]["url"]
        conn = psycopg2.connect(url, connect_timeout=10)
        conn.autocommit = False
        _stworz_tabele(conn)
        return conn
    except Exception as e:
        st.error(f"❌ Błąd połączenia z Supabase: {e}")
        return None

def _kursor(conn):
    """Zwraca kursor zwracający słowniki (DictCursor)."""
    return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

# ---------------------------------------------------------------------------
# SCHEMAT BAZY — tworzy się automatycznie
# ---------------------------------------------------------------------------
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS dokumenty (
    id          TEXT PRIMARY KEY,
    sygnatura   TEXT        NOT NULL,
    podatek     TEXT        NOT NULL,
    data_wyd    TEXT        NOT NULL,
    link        TEXT        NOT NULL,
    tekst       TEXT        NOT NULL,
    format_zr   TEXT        DEFAULT 'HTML+PDF',
    pobrano_kto TEXT        DEFAULT 'system',
    pobrano_dt  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_dok_podatek_data ON dokumenty(podatek, data_wyd);
CREATE INDEX IF NOT EXISTS idx_dok_sygnatura    ON dokumenty(sygnatura);

CREATE TABLE IF NOT EXISTS kombinacje_ukonczone (
    klucz           TEXT PRIMARY KEY,
    data_skanowania TIMESTAMPTZ DEFAULT NOW()
);
"""

def _stworz_tabele(conn):
    with _kursor(conn) as cur:
        cur.execute(SCHEMA_SQL)
    conn.commit()

# ---------------------------------------------------------------------------
# ZAPIS
# ---------------------------------------------------------------------------
def zapisz_wiele_do_archiwum(rekordy: list, pobrano_kto: str = "system") -> int:
    """
    Masowy zapis rekordów. Pomija duplikaty (ON CONFLICT DO NOTHING).
    Zwraca liczbę faktycznie nowych rekordów.
    """
    if not rekordy:
        return 0
    conn = _polaczenie()
    if not conn:
        return 0

    dane = [
        (
            _id_z_rekordu(r),
            r["Sygnatura"],
            r["Podatek"],
            r["Data"],
            r["Link"],
            r["Tekst"],
            r.get("Format", "HTML+PDF"),
            pobrano_kto,
        )
        for r in rekordy
    ]

    sql = """
        INSERT INTO dokumenty (id, sygnatura, podatek, data_wyd, link, tekst, format_zr, pobrano_kto)
        VALUES %s
        ON CONFLICT (id) DO NOTHING
    """
    with _db_lock:
        try:
            with _kursor(conn) as cur:
                psycopg2.extras.execute_values(cur, sql, dane, page_size=200)
                nowych = cur.rowcount
            conn.commit()
            # Wyczyść cache po zapisie
            pobierz_id_z_archiwum.clear()
            return nowych
        except Exception as e:
            conn.rollback()
            st.warning(f"⚠️ Błąd zapisu do Supabase: {e}")
            return 0

# ---------------------------------------------------------------------------
# KOMBINACJE UKOŃCZONE
# ---------------------------------------------------------------------------
def oznacz_kombinacje(podatek: str, rok: int, miesiac: int):
    """Oznacza period jako w pełni pobrany."""
    klucz = _klucz_kombinacji(podatek, rok, miesiac)
    conn  = _polaczenie()
    if not conn:
        return
    with _db_lock:
        try:
            with _kursor(conn) as cur:
                cur.execute(
                    "INSERT INTO kombinacje_ukonczone (klucz) VALUES (%s) ON CONFLICT DO NOTHING",
                    (klucz,)
                )
            conn.commit()
            pobierz_ukonczone_kombinacje.clear()
        except Exception as e:
            conn.rollback()
            st.warning(f"⚠️ Błąd oznaczania kombinacji: {e}")

@st.cache_data(ttl=120, show_spinner=False)
def pobierz_ukonczone_kombinacje() -> set:
    """Zwraca set kluczy kombinacji w pełni pobranych. Cache 2 minuty."""
    conn = _polaczenie()
    if not conn:
        return set()
    with _kursor(conn) as cur:
        cur.execute("SELECT klucz FROM kombinacje_ukonczone")
        return {r["klucz"] for r in cur.fetchall()}

# ---------------------------------------------------------------------------
# ODCZYT
# ---------------------------------------------------------------------------
@st.cache_data(ttl=60, show_spinner=False)
def pobierz_id_z_archiwum() -> set:
    """
    Zwraca set wszystkich ID dokumentów w archiwum.
    Cache 60 sekund — błyskawiczne sprawdzanie duplikatów.
    """
    conn = _polaczenie()
    if not conn:
        return set()
    with _kursor(conn) as cur:
        cur.execute("SELECT id FROM dokumenty")
        return {r["id"] for r in cur.fetchall()}

def pobierz_rekordy_z_archiwum(
    podatek: str = None,
    rok: int = None,
    miesiac: int = None,
) -> list:
    """
    Pobiera rekordy z bazy z opcjonalnym filtrowaniem.
    Zwraca listę słowników w formacie zgodnym z downloader.py.
    """
    conn = _polaczenie()
    if not conn:
        return []

    klauzule = []
    params   = []

    if podatek:
        klauzule.append("podatek = %s")
        params.append(podatek)
    if rok and miesiac:
        klauzule.append("data_wyd LIKE %s")
        params.append(f"{rok}-{miesiac:02d}%")
    elif rok:
        klauzule.append("data_wyd LIKE %s")
        params.append(f"{rok}%")

    where = f"WHERE {' AND '.join(klauzule)}" if klauzule else ""
    sql   = f"SELECT * FROM dokumenty {where} ORDER BY data_wyd DESC"

    with _kursor(conn) as cur:
        cur.execute(sql, params)
        return [_row_do_rekordu(r) for r in cur.fetchall()]

# ---------------------------------------------------------------------------
# STATYSTYKI
# ---------------------------------------------------------------------------
def statystyki_archiwum() -> dict:
    """Zwraca statystyki archiwum do wyświetlenia w UI."""
    conn = _polaczenie()
    if not conn:
        return {
            "total": 0, "per_podatek": {}, "ostatnie_pobranie": "—",
            "ukonczone_kombinacje": 0, "polaczenie": False
        }
    try:
        with _kursor(conn) as cur:
            cur.execute("SELECT COUNT(*) AS n FROM dokumenty")
            total = cur.fetchone()["n"]

            cur.execute(
                "SELECT podatek, COUNT(*) AS n FROM dokumenty GROUP BY podatek ORDER BY n DESC"
            )
            per_podatek = {r["podatek"]: r["n"] for r in cur.fetchall()}

            cur.execute(
                "SELECT pobrano_dt FROM dokumenty ORDER BY pobrano_dt DESC LIMIT 1"
            )
            row_ost = cur.fetchone()
            ostatnie = str(row_ost["pobrano_dt"])[:10] if row_ost else "—"

            cur.execute("SELECT COUNT(*) AS n FROM kombinacje_ukonczone")
            ukonczone = cur.fetchone()["n"]

        return {
            "total":                total,
            "per_podatek":          per_podatek,
            "ostatnie_pobranie":    ostatnie,
            "ukonczone_kombinacje": ukonczone,
            "polaczenie":           True,
        }
    except Exception as e:
        return {
            "total": 0, "per_podatek": {}, "ostatnie_pobranie": "—",
            "ukonczone_kombinacje": 0, "polaczenie": False
        }

# ---------------------------------------------------------------------------
# POMOCNICZE
# ---------------------------------------------------------------------------
def _klucz_kombinacji(podatek: str, rok: int, miesiac: int) -> str:
    return f"{podatek}_{rok}_{miesiac:02d}"

def _id_z_rekordu(r: dict) -> str:
    if "_id" in r:
        return str(r["_id"])
    link  = r.get("Link", "")
    parts = link.rstrip("/").split("/")
    if parts and parts[-1].isdigit():
        return parts[-1]
    return hashlib.md5(link.encode()).hexdigest()[:16]

def _row_do_rekordu(row) -> dict:
    return {
        "Data":      row["data_wyd"],
        "Podatek":   row["podatek"],
        "Sygnatura": row["sygnatura"],
        "Link":      row["link"],
        "Tekst":     row["tekst"],
        "Format":    row["format_zr"],
        "Pobrano":   str(row.get("pobrano_dt", "")),
        "_id":       row["id"],
    }
