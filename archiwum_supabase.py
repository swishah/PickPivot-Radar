"""
archiwum_supabase.py

Secrets w Streamlit — wklej DOKLADNIE to (z Transaction pooler):
[supabase]
host     = "aws-0-eu-central-1.pooler.supabase.com"
port     = "6543"
database = "postgres"
user     = "postgres.elidggvexfttbilxkwry"
password = "TwojeHaslo"

ALBO jesli masz URL:
[supabase]
url = "postgresql://postgres.elidggvexfttbilxkwry:TwojeHaslo@aws-0-eu-central-1.pooler.supabase.com:6543/postgres"
"""

import streamlit as st
import hashlib
import threading
from datetime import datetime

_db_lock = threading.Lock()


# ---------------------------------------------------------------------------
# POLACZENIE — uzywamy urllib do parsowania, psycopg2 do polaczenia
# ---------------------------------------------------------------------------
def _parametry() -> dict:
    """Czyta parametry z Secrets. Obsluguje format {url} i {host/user/...}"""
    s = st.secrets["supabase"]

    # Format 1: osobne pola host/port/user/password
    if s.get("host"):
        return {
            "host":    s["host"],
            "port":    int(s.get("port", 6543)),
            "dbname":  s.get("database", "postgres"),
            "user":    s["user"],
            "password":s["password"],
            "sslmode": "require",
            "connect_timeout": 15,
        }

    # Format 2: url — parsujemy recznie zeby uniknac problemow z urllib i @
    url = s["url"]  # postgresql://user:pass@host:port/db
    # Wycinamy czesci po postgresql://
    bez_schematu = url.split("://", 1)[1]
    # user:pass@host:port/db
    at_idx   = bez_schematu.rfind("@")   # ostatnie @ (haslo moze zawierac @)
    user_pass = bez_schematu[:at_idx]
    host_rest = bez_schematu[at_idx+1:]
    # user:pass
    colon_idx = user_pass.index(":")
    user      = user_pass[:colon_idx]
    password  = user_pass[colon_idx+1:]
    # host:port/db
    slash_idx = host_rest.index("/")
    host_port = host_rest[:slash_idx]
    dbname    = host_rest[slash_idx+1:]
    if ":" in host_port:
        host, port = host_port.rsplit(":", 1)
        port = int(port)
    else:
        host, port = host_port, 6543

    return {
        "host":    host,
        "port":    port,
        "dbname":  dbname,
        "user":    user,
        "password":password,
        "sslmode": "require",
        "connect_timeout": 15,
    }


def _nowe_polaczenie():
    import psycopg2
    p = _parametry()
    conn = psycopg2.connect(**p)
    conn.autocommit = False
    return conn


# ---------------------------------------------------------------------------
# SCHEMAT
# ---------------------------------------------------------------------------
SCHEMA = [
    """CREATE TABLE IF NOT EXISTS dokumenty (
        id TEXT PRIMARY KEY, sygnatura TEXT NOT NULL,
        podatek TEXT NOT NULL, data_wyd TEXT NOT NULL,
        link TEXT NOT NULL, tekst TEXT NOT NULL,
        format_zr TEXT DEFAULT 'HTML+PDF',
        pobrano_kto TEXT DEFAULT 'system',
        pobrano_dt TEXT DEFAULT '')""",
    "CREATE INDEX IF NOT EXISTS idx_d1 ON dokumenty(podatek, data_wyd)",
    "CREATE INDEX IF NOT EXISTS idx_d2 ON dokumenty(sygnatura)",
    """CREATE TABLE IF NOT EXISTS kombinacje_ukonczone (
        klucz TEXT PRIMARY KEY,
        data_skanowania TEXT DEFAULT '')""",
]


@st.cache_resource(show_spinner=False)
def _inicjalizuj():
    try:
        conn = _nowe_polaczenie()
        with conn.cursor() as cur:
            for sql in SCHEMA:
                cur.execute(sql)
        conn.commit(); conn.close()
        return True
    except Exception as e:
        st.error(f"Blad inicjalizacji bazy Supabase: {e}")
        return False


def _q(sql, params=None, fetch=False):
    """Wykonuje zapytanie na nowym polaczeniu."""
    conn = _nowe_polaczenie()
    try:
        import psycopg2.extras
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            if fetch:
                wynik = [dict(r) for r in cur.fetchall()]
                conn.commit()
                return wynik
            n = cur.rowcount
        conn.commit()
        return n
    except Exception:
        conn.rollback(); raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# ZAPIS
# ---------------------------------------------------------------------------
def zapisz_wiele_do_archiwum(rekordy: list, pobrano_kto: str = "system") -> int:
    if not rekordy: return 0
    _inicjalizuj()
    import psycopg2.extras
    dane = [(
        _id_z_rekordu(r), r["Sygnatura"], r["Podatek"], r["Data"],
        r["Link"], r["Tekst"], r.get("Format","HTML+PDF"),
        pobrano_kto, datetime.now().isoformat(timespec="seconds")
    ) for r in rekordy]

    sql = """INSERT INTO dokumenty
        (id,sygnatura,podatek,data_wyd,link,tekst,format_zr,pobrano_kto,pobrano_dt)
        VALUES %s ON CONFLICT (id) DO NOTHING"""
    conn = _nowe_polaczenie()
    try:
        with conn.cursor() as cur:
            psycopg2.extras.execute_values(cur, sql, dane, page_size=100)
            n = cur.rowcount
        conn.commit()
        pobierz_id_z_archiwum.clear()
        return n
    except Exception as e:
        conn.rollback()
        st.warning(f"Blad zapisu: {e}"); return 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# KOMBINACJE
# ---------------------------------------------------------------------------
def oznacz_kombinacje(podatek, rok, miesiac):
    _inicjalizuj()
    try:
        _q("INSERT INTO kombinacje_ukonczone (klucz,data_skanowania) VALUES (%s,%s) ON CONFLICT (klucz) DO NOTHING",
           (_klucz_kombinacji(podatek,rok,miesiac), datetime.now().isoformat(timespec="seconds")))
        pobierz_ukonczone_kombinacje.clear()
    except Exception as e:
        st.warning(f"Blad oznaczania: {e}")


@st.cache_data(ttl=120, show_spinner=False)
def pobierz_ukonczone_kombinacje() -> set:
    _inicjalizuj()
    try:
        rows = _q("SELECT klucz FROM kombinacje_ukonczone", fetch=True)
        return {r["klucz"] for r in rows}
    except Exception: return set()


# ---------------------------------------------------------------------------
# ODCZYT
# ---------------------------------------------------------------------------
@st.cache_data(ttl=60, show_spinner=False)
def pobierz_id_z_archiwum() -> set:
    _inicjalizuj()
    try:
        rows = _q("SELECT id FROM dokumenty", fetch=True)
        return {r["id"] for r in rows}
    except Exception: return set()


def pobierz_rekordy_z_archiwum(podatek=None, rok=None, miesiac=None) -> list:
    _inicjalizuj()
    kl, pa = [], []
    if podatek:
        kl.append("podatek = %s"); pa.append(podatek)
    if rok and miesiac:
        kl.append("data_wyd LIKE %s"); pa.append(f"{rok}-{miesiac:02d}%")
    elif rok:
        kl.append("data_wyd LIKE %s"); pa.append(f"{rok}%")
    where = f"WHERE {' AND '.join(kl)}" if kl else ""
    try:
        rows = _q(f"SELECT * FROM dokumenty {where} ORDER BY data_wyd DESC",
                  pa if pa else None, fetch=True)
        return [_row(r) for r in rows]
    except Exception as e:
        st.warning(f"Blad odczytu: {e}"); return []


# ---------------------------------------------------------------------------
# STATYSTYKI
# ---------------------------------------------------------------------------
def statystyki_archiwum() -> dict:
    try:
        _inicjalizuj()
        total = _q("SELECT COUNT(*) AS n FROM dokumenty", fetch=True)[0]["n"]
        per   = {r["podatek"]: r["n"] for r in _q(
            "SELECT podatek, COUNT(*) AS n FROM dokumenty GROUP BY podatek ORDER BY n DESC",
            fetch=True)}
        ost   = _q("SELECT pobrano_dt FROM dokumenty ORDER BY pobrano_dt DESC LIMIT 1", fetch=True)
        uk    = _q("SELECT COUNT(*) AS n FROM kombinacje_ukonczone", fetch=True)[0]["n"]
        return {"total": total, "per_podatek": per,
                "ostatnie_pobranie": ost[0]["pobrano_dt"][:10] if ost else "—",
                "ukonczone_kombinacje": uk, "polaczenie": True}
    except Exception:
        return {"total":0,"per_podatek":{},"ostatnie_pobranie":"—",
                "ukonczone_kombinacje":0,"polaczenie":False}


# ---------------------------------------------------------------------------
# POMOCNICZE
# ---------------------------------------------------------------------------
def _klucz_kombinacji(podatek, rok, miesiac): return f"{podatek}_{rok}_{miesiac:02d}"

def _id_z_rekordu(r):
    if "_id" in r: return str(r["_id"])
    link  = r.get("Link","")
    parts = link.rstrip("/").split("/")
    if parts and parts[-1].isdigit(): return parts[-1]
    return hashlib.md5(link.encode()).hexdigest()[:16]

def _row(r):
    return {"Data": r.get("data_wyd",""), "Podatek": r.get("podatek",""),
            "Sygnatura": r.get("sygnatura",""), "Link": r.get("link",""),
            "Tekst": r.get("tekst",""), "Format": r.get("format_zr",""),
            "Pobrano": r.get("pobrano_dt",""), "_id": r.get("id","")}
