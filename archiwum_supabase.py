"""
archiwum_supabase.py — Archiwum interpretacji w Supabase (PostgreSQL).

Używa Session Pooler (port 5432 na aws pooler) + psycopg2.
Działa z Streamlit Cloud (IPv4) bez problemów z pg8000.

Secrets format:
[supabase]
host     = "aws-0-eu-central-1.pooler.supabase.com"
port     = "5432"
database = "postgres"
user     = "postgres.elidggvexfttbilxkwry"
password = "TwojeHaslo"
"""

import streamlit as st
import psycopg2
import psycopg2.extras
import threading
import hashlib
from datetime import datetime

_db_lock = threading.Lock()

# ---------------------------------------------------------------------------
# POŁĄCZENIE — osobne parametry zamiast URL (unika problemów z parsowaniem)
# ---------------------------------------------------------------------------
def _nowe_polaczenie():
    """
    Tworzy połączenie przez osobne parametry (host/user/pass).
    Nie używa URL — unika problemów z username zawierającym kropkę.
    """
    s = st.secrets["supabase"]
    conn = psycopg2.connect(
        host=s["host"],
        port=int(s.get("port", 5432)),
        dbname=s.get("database", "postgres"),
        user=s["user"],
        password=s["password"],
        sslmode="require",
        connect_timeout=15,
        options="-c statement_timeout=30000",
    )
    conn.autocommit = False
    return conn


# ---------------------------------------------------------------------------
# SCHEMAT BAZY
# ---------------------------------------------------------------------------
SCHEMA_SQLS = [
    """CREATE TABLE IF NOT EXISTS dokumenty (
        id          TEXT PRIMARY KEY,
        sygnatura   TEXT NOT NULL,
        podatek     TEXT NOT NULL,
        data_wyd    TEXT NOT NULL,
        link        TEXT NOT NULL,
        tekst       TEXT NOT NULL,
        format_zr   TEXT DEFAULT 'HTML+PDF',
        pobrano_kto TEXT DEFAULT 'system',
        pobrano_dt  TEXT DEFAULT ''
    )""",
    "CREATE INDEX IF NOT EXISTS idx_dok_pod_data ON dokumenty(podatek, data_wyd)",
    "CREATE INDEX IF NOT EXISTS idx_dok_syg ON dokumenty(sygnatura)",
    """CREATE TABLE IF NOT EXISTS kombinacje_ukonczone (
        klucz           TEXT PRIMARY KEY,
        data_skanowania TEXT DEFAULT ''
    )""",
]

@st.cache_resource(show_spinner=False)
def _inicjalizuj_baze():
    """Tworzy tabele przy pierwszym uruchomieniu. Odpala się raz per sesja."""
    try:
        conn = _nowe_polaczenie()
        with conn.cursor() as cur:
            for sql in SCHEMA_SQLS:
                cur.execute(sql)
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        st.error(f"❌ Błąd inicjalizacji bazy Supabase: {e}")
        return False


def _zapytaj(sql: str, params=None, fetch: bool = False):
    """Wykonuje jedno zapytanie na nowym połączeniu."""
    conn = _nowe_polaczenie()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            if fetch:
                wynik = [dict(r) for r in cur.fetchall()]
                conn.commit()
                return wynik
            affected = cur.rowcount
        conn.commit()
        return affected
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# ZAPIS
# ---------------------------------------------------------------------------
def zapisz_wiele_do_archiwum(rekordy: list, pobrano_kto: str = "system") -> int:
    if not rekordy:
        return 0
    _inicjalizuj_baze()

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
            datetime.now().isoformat(timespec="seconds"),
        )
        for r in rekordy
    ]

    sql = """
        INSERT INTO dokumenty
            (id, sygnatura, podatek, data_wyd, link, tekst, format_zr, pobrano_kto, pobrano_dt)
        VALUES %s
        ON CONFLICT (id) DO NOTHING
    """
    conn = _nowe_polaczenie()
    try:
        with conn.cursor() as cur:
            psycopg2.extras.execute_values(cur, sql, dane, page_size=100)
            nowych = cur.rowcount
        conn.commit()
        pobierz_id_z_archiwum.clear()
        return nowych
    except Exception as e:
        conn.rollback()
        st.warning(f"⚠️ Błąd zapisu do Supabase: {e}")
        return 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# KOMBINACJE
# ---------------------------------------------------------------------------
def oznacz_kombinacje(podatek: str, rok: int, miesiac: int):
    _inicjalizuj_baze()
    klucz = _klucz_kombinacji(podatek, rok, miesiac)
    try:
        _zapytaj(
            "INSERT INTO kombinacje_ukonczone (klucz, data_skanowania) VALUES (%s, %s) ON CONFLICT (klucz) DO NOTHING",
            (klucz, datetime.now().isoformat(timespec="seconds"))
        )
        pobierz_ukonczone_kombinacje.clear()
    except Exception as e:
        st.warning(f"⚠️ Błąd oznaczania kombinacji: {e}")


@st.cache_data(ttl=120, show_spinner=False)
def pobierz_ukonczone_kombinacje() -> set:
    _inicjalizuj_baze()
    try:
        rows = _zapytaj("SELECT klucz FROM kombinacje_ukonczone", fetch=True)
        return {r["klucz"] for r in rows}
    except Exception:
        return set()


# ---------------------------------------------------------------------------
# ODCZYT
# ---------------------------------------------------------------------------
@st.cache_data(ttl=60, show_spinner=False)
def pobierz_id_z_archiwum() -> set:
    _inicjalizuj_baze()
    try:
        rows = _zapytaj("SELECT id FROM dokumenty", fetch=True)
        return {r["id"] for r in rows}
    except Exception:
        return set()


def pobierz_rekordy_z_archiwum(
    podatek: str = None,
    rok: int = None,
    miesiac: int = None,
) -> list:
    _inicjalizuj_baze()
    klauzule, params = [], []
    if podatek:
        klauzule.append("podatek = %s"); params.append(podatek)
    if rok and miesiac:
        klauzule.append("data_wyd LIKE %s"); params.append(f"{rok}-{miesiac:02d}%")
    elif rok:
        klauzule.append("data_wyd LIKE %s"); params.append(f"{rok}%")

    where = f"WHERE {' AND '.join(klauzule)}" if klauzule else ""
    sql   = f"SELECT * FROM dokumenty {where} ORDER BY data_wyd DESC"
    try:
        rows = _zapytaj(sql, params if params else None, fetch=True)
        return [_row_do_rekordu(r) for r in rows]
    except Exception as e:
        st.warning(f"⚠️ Błąd odczytu: {e}")
        return []


# ---------------------------------------------------------------------------
# STATYSTYKI
# ---------------------------------------------------------------------------
def statystyki_archiwum() -> dict:
    try:
        _inicjalizuj_baze()
        total     = _zapytaj("SELECT COUNT(*) AS n FROM dokumenty", fetch=True)[0]["n"]
        per_pod   = {r["podatek"]: r["n"] for r in _zapytaj(
            "SELECT podatek, COUNT(*) AS n FROM dokumenty GROUP BY podatek ORDER BY n DESC",
            fetch=True)}
        ost_rows  = _zapytaj(
            "SELECT pobrano_dt FROM dokumenty ORDER BY pobrano_dt DESC LIMIT 1", fetch=True)
        ostatnie  = ost_rows[0]["pobrano_dt"][:10] if ost_rows else "—"
        ukonczone = _zapytaj("SELECT COUNT(*) AS n FROM kombinacje_ukonczone", fetch=True)[0]["n"]
        return {"total": total, "per_podatek": per_pod, "ostatnie_pobranie": ostatnie,
                "ukonczone_kombinacje": ukonczone, "polaczenie": True}
    except Exception:
        return {"total": 0, "per_podatek": {}, "ostatnie_pobranie": "—",
                "ukonczone_kombinacje": 0, "polaczenie": False}


# ---------------------------------------------------------------------------
# POMOCNICZE
# ---------------------------------------------------------------------------
def _klucz_kombinacji(podatek: str, rok: int, miesiac: int) -> str:
    return f"{podatek}_{rok}_{miesiac:02d}"

def _id_z_rekordu(r: dict) -> str:
    if "_id" in r: return str(r["_id"])
    link  = r.get("Link", "")
    parts = link.rstrip("/").split("/")
    if parts and parts[-1].isdigit(): return parts[-1]
    return hashlib.md5(link.encode()).hexdigest()[:16]

def _row_do_rekordu(row: dict) -> dict:
    return {
        "Data":      row.get("data_wyd", ""),
        "Podatek":   row.get("podatek", ""),
        "Sygnatura": row.get("sygnatura", ""),
        "Link":      row.get("link", ""),
        "Tekst":     row.get("tekst", ""),
        "Format":    row.get("format_zr", ""),
        "Pobrano":   row.get("pobrano_dt", ""),
        "_id":       row.get("id", ""),
    }
