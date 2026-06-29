"""
archiwum_supabase.py — Archiwum interpretacji w Supabase (PostgreSQL).

WAŻNE: Streamlit Cloud nie obsługuje IPv6, więc NIE używaj "Direct connection".
Używaj "Transaction pooler" z portem 6543 — działa przez IPv4.

Connection string w Streamlit Secrets powinien wyglądać tak:
[supabase]
url = "postgresql://postgres.TWOJ_ID:HASLO@aws-0-eu-central-1.pooler.supabase.com:6543/postgres"
"""

import streamlit as st
import threading
import hashlib
from datetime import datetime

# Używamy pg8000 zamiast psycopg2 — działa lepiej z Supabase Pooler na Streamlit Cloud
# Fallback na psycopg2 jeśli pg8000 nie jest dostępne
try:
    import pg8000.native as pg_native
    import pg8000
    _DRIVER = "pg8000"
except ImportError:
    try:
        import psycopg2
        import psycopg2.extras
        _DRIVER = "psycopg2"
    except ImportError:
        _DRIVER = None

_db_lock = threading.Lock()

# ---------------------------------------------------------------------------
# PARSOWANIE URL
# ---------------------------------------------------------------------------
def _parsuj_url(url: str) -> dict:
    """
    Parsuje postgresql://user:pass@host:port/db
    Zwraca słownik z parametrami połączenia.
    """
    import re
    m = re.match(
        r"postgresql(?:\+\w+)?://([^:]+):([^@]+)@([^:/]+):(\d+)/(.+)",
        url.strip()
    )
    if not m:
        raise ValueError(f"Nieprawidłowy format URL: {url[:40]}...")
    return {
        "user":     m.group(1),
        "password": m.group(2),
        "host":     m.group(3),
        "port":     int(m.group(4)),
        "database": m.group(5),
    }

# ---------------------------------------------------------------------------
# POŁĄCZENIE — nowe przy każdym wywołaniu (pooler jest stateless)
# ---------------------------------------------------------------------------
def _nowe_polaczenie():
    """
    Tworzy nowe połączenie z bazą.
    Transaction pooler wymaga nowego połączenia per request — nie cachujemy.
    """
    url = st.secrets["supabase"]["url"]
    params = _parsuj_url(url)

    if _DRIVER == "pg8000":
        conn = pg8000.connect(
            user=params["user"],
            password=params["password"],
            host=params["host"],
            port=params["port"],
            database=params["database"],
            ssl_context=True,        # wymagane przez Supabase
            timeout=15,
        )
        return conn, "pg8000"

    elif _DRIVER == "psycopg2":
        conn = psycopg2.connect(
            user=params["user"],
            password=params["password"],
            host=params["host"],
            port=params["port"],
            dbname=params["database"],
            sslmode="require",       # wymagane przez Supabase
            connect_timeout=15,
        )
        conn.autocommit = False
        return conn, "psycopg2"

    else:
        raise RuntimeError("Brak sterownika PostgreSQL. Dodaj 'pg8000' lub 'psycopg2-binary' do requirements.txt")


def _wykonaj(sql: str, params=None, fetch=False):
    """
    Wykonuje zapytanie SQL na nowym połączeniu.
    Zwraca listę słowników (dla SELECT) lub liczbę zmienionych wierszy (dla INSERT/UPDATE).
    """
    conn, driver = _nowe_polaczenie()
    try:
        if driver == "pg8000":
            rows = conn.run(sql, **({"parameters": list(params)} if params else {}))
            if fetch:
                cols = [c["name"] for c in conn.columns]
                return [dict(zip(cols, row)) for row in rows]
            return conn.row_count

        else:  # psycopg2
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, params)
                if fetch:
                    return [dict(r) for r in cur.fetchall()]
                affected = cur.rowcount
            conn.commit()
            return affected

    finally:
        try:
            conn.close()
        except Exception:
            pass


def _wykonaj_wiele(sql: str, dane: list):
    """Masowy INSERT przez executemany / execute_values."""
    if not dane:
        return 0
    conn, driver = _nowe_polaczenie()
    try:
        if driver == "pg8000":
            affected = 0
            for row in dane:
                conn.run(sql, parameters=list(row))
                affected += conn.row_count
            return affected
        else:
            with conn.cursor() as cur:
                psycopg2.extras.execute_values(cur, sql, dane, page_size=100)
                affected = cur.rowcount
            conn.commit()
            return affected
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# SCHEMAT BAZY — tworzy się automatycznie
# ---------------------------------------------------------------------------
SCHEMA_SQL = [
    """
    CREATE TABLE IF NOT EXISTS dokumenty (
        id          TEXT PRIMARY KEY,
        sygnatura   TEXT NOT NULL,
        podatek     TEXT NOT NULL,
        data_wyd    TEXT NOT NULL,
        link        TEXT NOT NULL,
        tekst       TEXT NOT NULL,
        format_zr   TEXT DEFAULT 'HTML+PDF',
        pobrano_kto TEXT DEFAULT 'system',
        pobrano_dt  TEXT DEFAULT ''
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_dok_pod_data ON dokumenty(podatek, data_wyd)",
    "CREATE INDEX IF NOT EXISTS idx_dok_syg ON dokumenty(sygnatura)",
    """
    CREATE TABLE IF NOT EXISTS kombinacje_ukonczone (
        klucz           TEXT PRIMARY KEY,
        data_skanowania TEXT DEFAULT ''
    )
    """,
]

@st.cache_resource(show_spinner=False)
def _inicjalizuj_baze():
    """Tworzy tabele przy pierwszym uruchomieniu. Cachowane — odpala się raz."""
    try:
        for sql in SCHEMA_SQL:
            _wykonaj(sql.strip())
        return True
    except Exception as e:
        st.error(f"❌ Błąd inicjalizacji bazy Supabase: {e}")
        return False

# ---------------------------------------------------------------------------
# ZAPIS
# ---------------------------------------------------------------------------
def zapisz_wiele_do_archiwum(rekordy: list, pobrano_kto: str = "system") -> int:
    """Masowy zapis rekordów. Pomija duplikaty. Zwraca liczbę nowych."""
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
        INSERT INTO dokumenty (id, sygnatura, podatek, data_wyd, link, tekst, format_zr, pobrano_kto, pobrano_dt)
        VALUES %s
        ON CONFLICT (id) DO NOTHING
    """
    # pg8000 nie obsługuje execute_values — robimy jeden po jednym
    conn, driver = _nowe_polaczenie()
    nowych = 0
    try:
        if driver == "pg8000":
            sql_single = """
                INSERT INTO dokumenty (id, sygnatura, podatek, data_wyd, link, tekst, format_zr, pobrano_kto, pobrano_dt)
                VALUES (:1, :2, :3, :4, :5, :6, :7, :8, :9)
                ON CONFLICT (id) DO NOTHING
            """
            # pg8000 używa %s jako placeholder
            sql_pg8 = """
                INSERT INTO dokumenty (id, sygnatura, podatek, data_wyd, link, tekst, format_zr, pobrano_kto, pobrano_dt)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO NOTHING
            """
            for row in dane:
                conn.run(
                    "INSERT INTO dokumenty (id,sygnatura,podatek,data_wyd,link,tekst,format_zr,pobrano_kto,pobrano_dt) "
                    "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9) ON CONFLICT (id) DO NOTHING",
                    parameters=list(row)
                )
                nowych += conn.row_count
        else:
            with conn.cursor() as cur:
                psycopg2.extras.execute_values(cur, sql, dane, page_size=100)
                nowych = cur.rowcount
            conn.commit()
    except Exception as e:
        st.warning(f"⚠️ Błąd zapisu do Supabase: {e}")
    finally:
        try: conn.close()
        except: pass

    # Wyczyść cache po zapisie
    pobierz_id_z_archiwum.clear()
    return nowych

# ---------------------------------------------------------------------------
# KOMBINACJE UKOŃCZONE
# ---------------------------------------------------------------------------
def oznacz_kombinacje(podatek: str, rok: int, miesiac: int):
    """Oznacza period jako w pełni pobrany."""
    _inicjalizuj_baze()
    klucz = _klucz_kombinacji(podatek, rok, miesiac)
    try:
        conn, driver = _nowe_polaczenie()
        if driver == "pg8000":
            conn.run(
                "INSERT INTO kombinacje_ukonczone (klucz, data_skanowania) VALUES ($1, $2) ON CONFLICT (klucz) DO NOTHING",
                parameters=[klucz, datetime.now().isoformat(timespec="seconds")]
            )
        else:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO kombinacje_ukonczone (klucz, data_skanowania) VALUES (%s, %s) ON CONFLICT (klucz) DO NOTHING",
                    (klucz, datetime.now().isoformat(timespec="seconds"))
                )
            conn.commit()
        conn.close()
        pobierz_ukonczone_kombinacje.clear()
    except Exception as e:
        st.warning(f"⚠️ Błąd oznaczania kombinacji: {e}")


@st.cache_data(ttl=120, show_spinner=False)
def pobierz_ukonczone_kombinacje() -> set:
    """Zwraca set kluczy ukończonych kombinacji. Cache 2 minuty."""
    _inicjalizuj_baze()
    try:
        rows = _wykonaj("SELECT klucz FROM kombinacje_ukonczone", fetch=True)
        return {r["klucz"] for r in rows}
    except Exception:
        return set()

# ---------------------------------------------------------------------------
# ODCZYT
# ---------------------------------------------------------------------------
@st.cache_data(ttl=60, show_spinner=False)
def pobierz_id_z_archiwum() -> set:
    """Zwraca set wszystkich ID dokumentów. Cache 60 sekund."""
    _inicjalizuj_baze()
    try:
        rows = _wykonaj("SELECT id FROM dokumenty", fetch=True)
        return {r["id"] for r in rows}
    except Exception:
        return set()


def pobierz_rekordy_z_archiwum(
    podatek: str = None,
    rok: int = None,
    miesiac: int = None,
) -> list:
    """Pobiera rekordy z opcjonalnym filtrowaniem."""
    _inicjalizuj_baze()
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

    try:
        # Dla pg8000 trzeba zamienić %s na $1, $2...
        conn, driver = _nowe_polaczenie()
        if driver == "pg8000":
            sql_pg8 = sql
            for i in range(len(params)):
                sql_pg8 = sql_pg8.replace("%s", f"${i+1}", 1)
            rows = conn.run(sql_pg8, parameters=params if params else None)
            cols = [c["name"] for c in conn.columns]
            result = [dict(zip(cols, row)) for row in rows]
            conn.close()
        else:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, params if params else None)
                result = [dict(r) for r in cur.fetchall()]
            conn.close()
        return [_row_do_rekordu(r) for r in result]
    except Exception as e:
        st.warning(f"⚠️ Błąd odczytu z archiwum: {e}")
        return []

# ---------------------------------------------------------------------------
# STATYSTYKI
# ---------------------------------------------------------------------------
def statystyki_archiwum() -> dict:
    """Zwraca statystyki do wyświetlenia w UI."""
    try:
        _inicjalizuj_baze()
        total_rows    = _wykonaj("SELECT COUNT(*) AS n FROM dokumenty", fetch=True)
        total         = total_rows[0]["n"] if total_rows else 0

        per_pod_rows  = _wykonaj(
            "SELECT podatek, COUNT(*) AS n FROM dokumenty GROUP BY podatek ORDER BY n DESC",
            fetch=True
        )
        per_podatek   = {r["podatek"]: r["n"] for r in per_pod_rows}

        ost_rows      = _wykonaj(
            "SELECT pobrano_dt FROM dokumenty ORDER BY pobrano_dt DESC LIMIT 1",
            fetch=True
        )
        ostatnie      = ost_rows[0]["pobrano_dt"][:10] if ost_rows else "—"

        uk_rows       = _wykonaj("SELECT COUNT(*) AS n FROM kombinacje_ukonczone", fetch=True)
        ukonczone     = uk_rows[0]["n"] if uk_rows else 0

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
            "ukonczone_kombinacje": 0, "polaczenie": False,
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
