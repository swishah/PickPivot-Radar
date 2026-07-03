"""
db_core.py — Rdzeń logiki bazodanowej Supabase, NIEZALEZNY od Streamlit.

Uzywany przez:
  - archiwum_supabase.py  (warstwa Streamlit z cache)
  - raport_tygodniowy.py  (skrypt GitHub Actions, brak Streamlit)

Parametry polaczenia czytane sa z dict przekazanego w konstruktorze,
zeby kazdy z dwoch kontekstow mogl dostarczyc je na swoj sposob
(st.secrets w Streamlit, os.environ w GitHub Actions).
"""

import hashlib
import threading
from datetime import datetime, timedelta

_db_lock = threading.Lock()


# ---------------------------------------------------------------------------
# POLACZENIE
# ---------------------------------------------------------------------------
def parametry_z_url(url: str) -> dict:
    """Parsuje postgresql://user:pass@host:port/db recznie (omija problemy urllib z @ i .)"""
    bez_schematu = url.split("://", 1)[1]
    at_idx    = bez_schematu.rfind("@")
    user_pass = bez_schematu[:at_idx]
    host_rest = bez_schematu[at_idx + 1:]
    colon_idx = user_pass.index(":")
    user      = user_pass[:colon_idx]
    password  = user_pass[colon_idx + 1:]
    slash_idx = host_rest.index("/")
    host_port = host_rest[:slash_idx]
    dbname    = host_rest[slash_idx + 1:]
    if ":" in host_port:
        host, port = host_port.rsplit(":", 1)
        port = int(port)
    else:
        host, port = host_port, 6543
    return {
        "host": host, "port": port, "dbname": dbname,
        "user": user, "password": password,
        "sslmode": "require", "connect_timeout": 15,
    }


class SupabaseDB:
    """
    Klasa opakowujaca polaczenia z Supabase.
    Tworzy NOWE polaczenie przy kazdym zapytaniu (wymagane przez pooler).
    """

    def __init__(self, parametry: dict):
        """
        parametry: dict z kluczami host/port/dbname/user/password
                   LUB {"url": "postgresql://..."}
        """
        if "url" in parametry and parametry["url"]:
            self._params = parametry_z_url(parametry["url"])
        else:
            self._params = {
                "host":     parametry["host"],
                "port":     int(parametry.get("port", 6543)),
                "dbname":   parametry.get("database", "postgres"),
                "user":     parametry["user"],
                "password": parametry["password"],
                "sslmode":  "require",
                "connect_timeout": 15,
            }

    def _polacz(self):
        import psycopg2
        conn = psycopg2.connect(**self._params)
        conn.autocommit = False
        return conn

    def wykonaj(self, sql: str, params=None, fetch: bool = False):
        """Wykonuje pojedyncze zapytanie na nowym polaczeniu."""
        import psycopg2.extras
        conn = self._polacz()
        try:
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
            conn.rollback()
            raise
        finally:
            conn.close()

    def wykonaj_wiele(self, sql_z_values: str, dane: list) -> int:
        """INSERT ... VALUES %s przez execute_values."""
        if not dane:
            return 0
        import psycopg2.extras
        conn = self._polacz()
        try:
            with conn.cursor() as cur:
                psycopg2.extras.execute_values(cur, sql_z_values, dane, page_size=100)
                n = cur.rowcount
            conn.commit()
            return n
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def inicjalizuj_schemat(self):
        """Tworzy wszystkie wymagane tabele jesli nie istnieja."""
        for sql in SCHEMA_SQL:
            self.wykonaj(sql)


# ---------------------------------------------------------------------------
# SCHEMAT BAZY (wspolny dla obu tabel: dokumenty + raporty tygodniowe)
# ---------------------------------------------------------------------------
SCHEMA_SQL = [
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

    # ── NOWA TABELA: raporty tygodniowe (pliki Word gotowe do pobrania) ──
    """CREATE TABLE IF NOT EXISTS raporty_tygodniowe (
        id            SERIAL PRIMARY KEY,
        tydzien_klucz TEXT NOT NULL,         -- np. "2026-W05" (rok-numer_tygodnia)
        data_od       TEXT NOT NULL,         -- poniedzialek YYYY-MM-DD
        data_do       TEXT NOT NULL,         -- piatek YYYY-MM-DD
        podatek       TEXT NOT NULL,         -- PIT / CIT / VAT / AKCYZA
        liczba_dok    INTEGER DEFAULT 0,
        plik_word     BYTEA,                 -- zawartosc .docx jako binarne dane
        nazwa_pliku   TEXT NOT NULL,
        wygenerowano  TEXT NOT NULL,         -- timestamp ISO
        UNIQUE(tydzien_klucz, podatek)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_r1 ON raporty_tygodniowe(tydzien_klucz)",

    # ── NOWA TABELA: historia uruchomien raportu na zadanie (status + weryfikacja) ──
    """CREATE TABLE IF NOT EXISTS historia_raportow_na_zadanie (
        id            SERIAL PRIMARY KEY,
        uruchomiono   TEXT NOT NULL,        -- timestamp ISO
        rok           INTEGER NOT NULL,
        miesiac       INTEGER NOT NULL,
        podatek       TEXT NOT NULL,         -- pojedynczy podatek lub "WSZYSTKIE"
        liczba_dok    INTEGER DEFAULT 0,
        liczba_prob   INTEGER DEFAULT 1,     -- ile prob calego raportu bylo potrzebnych
        status        TEXT NOT NULL,         -- OK / NIEZGODNOSC / WERYFIKACJA_NIEUDANA / ERROR
        szczegoly     TEXT DEFAULT ''        -- np. "MF: 45, archiwum: 43 (różnica: -2)"
    )""",
    "CREATE INDEX IF NOT EXISTS idx_h1 ON historia_raportow_na_zadanie(uruchomiono)",

    # ── NOWA TABELA: historia codziennej synchronizacji automatycznej (3:00) ──
    """CREATE TABLE IF NOT EXISTS historia_synchronizacji (
        id            SERIAL PRIMARY KEY,
        uruchomiono   TEXT NOT NULL,        -- timestamp ISO
        data_od       TEXT NOT NULL,        -- poczatek okna 3-dniowego YYYY-MM-DD
        data_do       TEXT NOT NULL,        -- koniec okna YYYY-MM-DD
        podatek       TEXT NOT NULL,
        liczba_dok    INTEGER DEFAULT 0,    -- lacznie w bazie dla tego okna
        nowych_dok    INTEGER DEFAULT 0,    -- ile nowych dodano w tym przebiegu
        liczba_prob   INTEGER DEFAULT 1,
        status        TEXT NOT NULL,        -- OK / NIEZGODNOSC / WERYFIKACJA_NIEUDANA / ERROR
        szczegoly     TEXT DEFAULT ''
    )""",
    "CREATE INDEX IF NOT EXISTS idx_s1 ON historia_synchronizacji(uruchomiono)",
]


# ---------------------------------------------------------------------------
# OPERACJE NA DOKUMENTACH (logika identyczna jak wczesniej, teraz w klasie)
# ---------------------------------------------------------------------------
def zapisz_wiele_do_archiwum(db: SupabaseDB, rekordy: list, pobrano_kto: str = "system") -> int:
    if not rekordy:
        return 0
    dane = [(
        _id_z_rekordu(r), r["Sygnatura"], r["Podatek"], r["Data"],
        r["Link"], r["Tekst"], r.get("Format", "HTML+PDF"),
        pobrano_kto, datetime.now().isoformat(timespec="seconds")
    ) for r in rekordy]
    sql = """INSERT INTO dokumenty
        (id,sygnatura,podatek,data_wyd,link,tekst,format_zr,pobrano_kto,pobrano_dt)
        VALUES %s ON CONFLICT (id) DO NOTHING"""
    return db.wykonaj_wiele(sql, dane)


def pobierz_rekordy_z_archiwum(db: SupabaseDB, podatek=None, rok=None, miesiac=None,
                                  data_od=None, data_do=None) -> list:
    """
    Pobiera rekordy z opcjonalnym filtrowaniem.
    data_od/data_do: stringi YYYY-MM-DD do filtrowania zakresu dat (uzywane dla raportow tygodniowych).
    """
    kl, pa = [], []
    if podatek:
        kl.append("podatek = %s"); pa.append(podatek)
    if data_od and data_do:
        kl.append("data_wyd >= %s AND data_wyd <= %s"); pa.extend([data_od, data_do])
    elif rok and miesiac:
        kl.append("data_wyd LIKE %s"); pa.append(f"{rok}-{miesiac:02d}%")
    elif rok:
        kl.append("data_wyd LIKE %s"); pa.append(f"{rok}%")
    where = f"WHERE {' AND '.join(kl)}" if kl else ""
    rows = db.wykonaj(f"SELECT * FROM dokumenty {where} ORDER BY data_wyd DESC",
                       pa if pa else None, fetch=True)
    return [_row_do_rekordu(r) for r in rows]


def pobierz_id_z_archiwum(db: SupabaseDB) -> set:
    rows = db.wykonaj("SELECT id FROM dokumenty", fetch=True)
    return {r["id"] for r in rows}


def oznacz_kombinacje(db: SupabaseDB, podatek: str, rok: int, miesiac: int):
    db.wykonaj(
        "INSERT INTO kombinacje_ukonczone (klucz,data_skanowania) VALUES (%s,%s) ON CONFLICT (klucz) DO NOTHING",
        (klucz_kombinacji(podatek, rok, miesiac), datetime.now().isoformat(timespec="seconds"))
    )


def pobierz_ukonczone_kombinacje(db: SupabaseDB) -> set:
    rows = db.wykonaj("SELECT klucz FROM kombinacje_ukonczone", fetch=True)
    return {r["klucz"] for r in rows}


def statystyki_archiwum(db: SupabaseDB) -> dict:
    sql = """
        SELECT
            (SELECT COUNT(*) FROM dokumenty) AS total,
            (SELECT COUNT(*) FROM kombinacje_ukonczone) AS ukonczone,
            (SELECT pobrano_dt FROM dokumenty ORDER BY pobrano_dt DESC LIMIT 1) AS ostatnie
    """
    row = db.wykonaj(sql, fetch=True)
    if not row:
        return {"total": 0, "per_podatek": {}, "ostatnie_pobranie": "—",
                "ukonczone_kombinacje": 0, "polaczenie": True}
    r = row[0]
    per = {p["podatek"]: p["n"] for p in db.wykonaj(
        "SELECT podatek, COUNT(*) AS n FROM dokumenty GROUP BY podatek ORDER BY n DESC", fetch=True)}
    ost = str(r["ostatnie"])[:10] if r["ostatnie"] else "—"
    return {"total": r["total"], "per_podatek": per, "ostatnie_pobranie": ost,
            "ukonczone_kombinacje": r["ukonczone"], "polaczenie": True}


def statystyki_szczegolowe(db: SupabaseDB) -> dict:
    """
    Rozszerzone statystyki dla modulu Archiwum: liczba dokumentow oraz
    zakres dat wydania (najstarsza/najnowsza interpretacja) per podatek.
    Odpowiada na pytanie "ile jest interpretacji, z kiedy i z jakiego podatku".
    """
    rows = db.wykonaj(
        """SELECT podatek, COUNT(*) AS liczba,
                  MIN(data_wyd) AS najstarsza, MAX(data_wyd) AS najnowsza
           FROM dokumenty
           GROUP BY podatek
           ORDER BY liczba DESC""",
        fetch=True
    )
    total = sum(r["liczba"] for r in rows) if rows else 0
    return {"total": total, "per_podatek": rows}


def rozklad_miesieczny(db: SupabaseDB, podatek: str = None) -> list:
    """
    Zwraca liczbe dokumentow pogrupowana wg rok-miesiac (i opcjonalnie
    dodatkowo wg podatku, jesli podatek=None). Uzywane do tabeli/wykresu
    rozkladu w czasie w module Archiwum.
    """
    kl, pa = [], []
    if podatek:
        kl.append("podatek = %s")
        pa.append(podatek)
    where = f"WHERE {' AND '.join(kl)}" if kl else ""
    return db.wykonaj(
        f"""SELECT podatek, LEFT(data_wyd, 7) AS rok_miesiac, COUNT(*) AS liczba
            FROM dokumenty {where}
            GROUP BY podatek, LEFT(data_wyd, 7)
            ORDER BY rok_miesiac DESC, podatek""",
        pa if pa else None, fetch=True
    )


# ---------------------------------------------------------------------------
# OPERACJE NA RAPORTACH TYGODNIOWYCH
# ---------------------------------------------------------------------------
def zapisz_raport_tygodniowy(
    db: SupabaseDB,
    tydzien_klucz: str,
    data_od: str,
    data_do: str,
    podatek: str,
    liczba_dok: int,
    plik_bytes: bytes,
    nazwa_pliku: str,
):
    """Zapisuje (lub nadpisuje) gotowy raport tygodniowy w bazie."""
    import psycopg2
    conn = db._polacz()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO raporty_tygodniowe
                    (tydzien_klucz, data_od, data_do, podatek, liczba_dok, plik_word, nazwa_pliku, wygenerowano)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (tydzien_klucz, podatek)
                DO UPDATE SET
                    liczba_dok   = EXCLUDED.liczba_dok,
                    plik_word    = EXCLUDED.plik_word,
                    nazwa_pliku  = EXCLUDED.nazwa_pliku,
                    wygenerowano = EXCLUDED.wygenerowano
                """,
                (
                    tydzien_klucz, data_od, data_do, podatek, liczba_dok,
                    psycopg2.Binary(plik_bytes), nazwa_pliku,
                    datetime.now().isoformat(timespec="seconds"),
                )
            )
        conn.commit()
    finally:
        conn.close()


def pobierz_liste_raportow(db: SupabaseDB) -> list:
    """Zwraca metadane wszystkich raportow (BEZ samego pliku - lzejsze zapytanie)."""
    rows = db.wykonaj(
        """SELECT id, tydzien_klucz, data_od, data_do, podatek, liczba_dok, nazwa_pliku, wygenerowano
           FROM raporty_tygodniowe
           ORDER BY tydzien_klucz DESC, podatek ASC""",
        fetch=True
    )
    return rows


def pobierz_plik_raportu(db: SupabaseDB, raport_id: int) -> bytes | None:
    """Pobiera zawartosc binarna konkretnego raportu po ID."""
    rows = db.wykonaj(
        "SELECT plik_word, nazwa_pliku FROM raporty_tygodniowe WHERE id = %s",
        (raport_id,), fetch=True
    )
    if not rows:
        return None, None
    plik = rows[0]["plik_word"]
    nazwa = rows[0]["nazwa_pliku"]
    # psycopg2 zwraca memoryview dla BYTEA
    if isinstance(plik, memoryview):
        plik = plik.tobytes()
    return plik, nazwa


# ---------------------------------------------------------------------------
# HISTORIA RAPORTOW NA ZADANIE — log statusow i wynikow weryfikacji
# ---------------------------------------------------------------------------
def zapisz_historie_raportu(
    db: SupabaseDB,
    rok: int,
    miesiac: int,
    podatek: str,
    liczba_dok: int,
    liczba_prob: int,
    status: str,
    szczegoly: str = "",
):
    """Zapisuje jeden wpis historii po zakonczeniu raportu na zadanie."""
    db.wykonaj(
        """INSERT INTO historia_raportow_na_zadanie
            (uruchomiono, rok, miesiac, podatek, liczba_dok, liczba_prob, status, szczegoly)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
        (datetime.now().isoformat(timespec="seconds"), rok, miesiac, podatek,
         liczba_dok, liczba_prob, status, szczegoly)
    )


def pobierz_historie_raportow(db: SupabaseDB, limit: int = 30) -> list:
    """Zwraca ostatnie N wpisow historii, najnowsze pierwsze."""
    return db.wykonaj(
        """SELECT * FROM historia_raportow_na_zadanie
           ORDER BY uruchomiono DESC LIMIT %s""",
        (limit,), fetch=True
    )


# ---------------------------------------------------------------------------
# HISTORIA SYNCHRONIZACJI DZIENNEJ — automatyczny job o 3:00
# ---------------------------------------------------------------------------
def zapisz_historie_synchronizacji(
    db: SupabaseDB,
    data_od: str,
    data_do: str,
    podatek: str,
    liczba_dok: int,
    nowych_dok: int,
    liczba_prob: int,
    status: str,
    szczegoly: str = "",
):
    """Zapisuje jeden wpis historii po zakonczeniu codziennej synchronizacji."""
    db.wykonaj(
        """INSERT INTO historia_synchronizacji
            (uruchomiono, data_od, data_do, podatek, liczba_dok, nowych_dok, liczba_prob, status, szczegoly)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (datetime.now().isoformat(timespec="seconds"), data_od, data_do, podatek,
         liczba_dok, nowych_dok, liczba_prob, status, szczegoly)
    )


def pobierz_historie_synchronizacji(db: SupabaseDB, limit: int = 30) -> list:
    """Zwraca ostatnie N wpisow historii synchronizacji, najnowsze pierwsze."""
    return db.wykonaj(
        """SELECT * FROM historia_synchronizacji
           ORDER BY uruchomiono DESC LIMIT %s""",
        (limit,), fetch=True
    )


# ---------------------------------------------------------------------------
# POMOCNICZE — daty i tygodnie
# ---------------------------------------------------------------------------
def klucz_kombinacji(podatek: str, rok: int, miesiac: int) -> str:
    return f"{podatek}_{rok}_{miesiac:02d}"


def klucz_tygodnia(data: datetime) -> str:
    """Zwraca klucz w formacie ISO 'RRRR-Wnn', np. '2026-W05'."""
    iso_rok, iso_tydzien, _ = data.isocalendar()
    return f"{iso_rok}-W{iso_tydzien:02d}"


def zakres_poprzedniego_tygodnia(dzisiaj: datetime = None) -> tuple:
    """
    Zwraca (poniedzialek, piatek, klucz_tygodnia) dla TYDZIEN POPRZEDZAJACY biezacy.
    Uzywane w sobote/niedziele/poniedzialek - zawsze liczy tydzien ktory wlasnie sie skonczyl
    (pon-pt sprzed weekendu na ktorym jestesmy).
    """
    if dzisiaj is None:
        dzisiaj = datetime.now()

    # Znajdz poniedzialek BIEZACEGO tygodnia
    poniedzialek_biezacy = dzisiaj - timedelta(days=dzisiaj.weekday())

    # Poprzedni tydzien = 7 dni wstecz
    poniedzialek_poprzedni = poniedzialek_biezacy - timedelta(days=7)
    piatek_poprzedni       = poniedzialek_poprzedni + timedelta(days=4)

    klucz = klucz_tygodnia(poniedzialek_poprzedni)
    return poniedzialek_poprzedni, piatek_poprzedni, klucz


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
