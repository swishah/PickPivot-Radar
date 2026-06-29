"""
archiwum_supabase.py — Trwałe archiwum interpretacji podatkowych w PostgreSQL (Supabase).
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
    return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

# ---------------------------------------------------------------------------
# SCHEMAT BAZY
# ---------------------------------------------------------------------------
def _stworz_tabele(conn):
    with _db_lock:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS interpretacje (
                    id_dokumentu VARCHAR(64) PRIMARY KEY,
                    sygnatura VARCHAR(255) NOT NULL,
                    podatek VARCHAR(50),
                    data_wyd DATE,
                    link TEXT,
                    tekst TEXT,
                    zrodlo VARCHAR(100),
                    data_dodania TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS kombinacje_ukonczone (
                    klucz VARCHAR(100) PRIMARY KEY,
                    data_zakonczenia TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_sygnatura ON interpretacje (sygnatura);
            """)
        conn.commit()

# ---------------------------------------------------------------------------
# INTERFEJS PUBLICZNY
# ---------------------------------------------------------------------------
def wczytaj_wiele_z_archiwum(lista_sygnatur: list) -> list:
    if not lista_sygnatur: return []
    conn = _polaczenie()
    if not conn: return []
    
    with _db_lock:
        try:
            with _kursor(conn) as cur:
                query = "SELECT * FROM interpretacje WHERE sygnatura = ANY(%s);"
                cur.execute(query, (lista_sygnatur,))
                rows = cur.fetchall()
                return [_row_do_rekordu(row) for row in rows]
        except Exception as e:
            conn.rollback()
            st.error(f"Błąd odczytu z bazy: {e}")
            return []

def zapisz_wiele_do_archiwum(rekordy: list, zrodlo: str = "downloader") -> int:
    if not rekordy: return 0
    conn = _polaczenie()
    if not conn: return 0
    
    wstawione = 0
    with _db_lock:
        try:
            with conn.cursor() as cur:
                query = """
                    INSERT INTO interpretacje (id_dokumentu, sygnatura, podatek, data_wyd, link, tekst, zrodlo)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id_dokumentu) DO NOTHING;
                """
                dane = []
                for r in rekordy:
                    d_id = _id_z_rekordu(r)
                    dane.append((d_id, r.get("Sygnatura"), r.get("Podatek"), r.get("Data"), r.get("Link"), r.get("Tekst"), zrodlo))
                
                psycopg2.extras.execute_batch(cur, query, dane)
                wstawione = cur.rowcount
            conn.commit()
            return wstawione
        except Exception as e:
            conn.rollback()
            st.error(f"Błąd zapisu do bazy: {e}")
            return 0

def oznacz_kombinacje(podatek: str, rok: int, miesiac: int):
    conn = _polaczenie()
    if not conn: return
    
    klucz = _klucz_kombinacji(podatek, rok, miesiac)
    with _db_lock:
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO kombinacje_ukonczone (klucz) VALUES (%s)
                    ON CONFLICT (klucz) DO NOTHING;
                """, (klucz,))
            conn.commit()
        except Exception:
            conn.rollback()

def pobierz_statystyki() -> dict:
    conn = _polaczenie()
    if not conn:
        return {"total": 0, "per_podatek": {}, "ostatnie_pobranie": "—", "ukonczone_kombinacje": 0, "polaczenie": False}
        
    try:
        with _kursor(conn) as cur:
            cur.execute("SELECT COUNT(*) AS n FROM interpretacje")
            total = cur.fetchone()["n"]

            cur.execute("SELECT podatek, COUNT(*) AS n FROM interpretacje GROUP BY podatek")
            per_podatek = {row["podatek"]: row["n"] for row in cur.fetchall()}

            cur.execute("SELECT MAX(data_dodania) AS ost FROM interpretacje")
            row_ost = cur.fetchone()
            ostatnie = str(row_ost["ost"])[:10] if row_ost and row_ost["ost"] else "—"

            cur.execute("SELECT COUNT(*) AS n FROM kombinacje_ukonczone")
            ukonczone = cur.fetchone()["n"]

        return {
            "total":                total,
            "per_podatek":          per_podatek,
            "ostatnie_pobranie":    ostatnie,
            "ukonczone_kombinacje": ukonczone,
            "polaczenie":           True,
        }
    except Exception:
        return {"total": 0, "per_podatek": {}, "ostatnie_pobranie": "—", "ukonczone_kombinacje": 0, "polaczenie": False}

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
        "Data":      str(row["data_wyd"]) if row["data_wyd"] else "",
        "Podatek":   row["podatek"],
        "Sygnatura": row["sygnatura"],
        "Link":      row["link"],
        "Tekst":     row["tekst"],
        "_id":       row["id_dokumentu"]
    }
