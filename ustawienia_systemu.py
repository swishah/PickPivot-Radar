# -*- coding: utf-8 -*-
"""
MODUŁ 8: Ustawienia Systemu — Skaner Doradca.
Na ten moment: BACKUP BAZY jednym kliknięciem.

Jak działa: moduł odpytuje wszystkie tabele aplikacji, pakuje dane do
jednego archiwum ZIP (CSV per tabela, w UTF-8) wraz z plikiem schema.sql
(struktury CREATE TABLE odtworzone z information_schema) i instrukcją
odtworzenia. Przycisk „Pobierz backup" zapisuje archiwum na dysk.

Uwaga techniczna: Streamlit Cloud nie ma narzędzia pg_dump (binarka
systemowa), dlatego eksport odbywa się po SQL-u. Do odtworzenia wystarczy
psql/Supabase SQL Editor (schema.sql) + import CSV (np. \\copy albo
Table Editor -> Import). Szczegóły w PRZYWRACANIE.txt wewnątrz archiwum.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import smtplib
import zipfile
from email.mime.text import MIMEText
from email.utils import formataddr

import streamlit as st

import archiwum_supabase
import auth

# Kolumna dokumenty.tekst_search jest GENEROWANA (tsvector) — nie eksportujemy
# jej (odtworzy się sama po imporcie) i pomijamy w schemacie.
KOLUMNY_POMIJANE = {("dokumenty", "tekst_search")}


def _zapytaj(sql: str, p: tuple | None = None) -> list[dict]:
    return archiwum_supabase._get_db().wykonaj(sql, p, fetch=True)


# ---------------------------------------------------------------------------
# INTROSPEKCJA SCHEMATU
# ---------------------------------------------------------------------------
def _lista_tabel() -> list[str]:
    rows = _zapytaj(
        """SELECT table_name FROM information_schema.tables
           WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
           ORDER BY table_name"""
    )
    return [r["table_name"] for r in rows]


def _kolumny(tabela: str) -> list[dict]:
    return _zapytaj(
        """SELECT column_name, data_type, is_nullable, column_default,
                  is_generated
           FROM information_schema.columns
           WHERE table_schema = 'public' AND table_name = %s
           ORDER BY ordinal_position""",
        (tabela,),
    )


def _schema_sql(tabele: list[str]) -> str:
    """Proste, odtwarzalne CREATE TABLE (bez kolumn generowanych)."""
    czesci = ["-- Schemat wygenerowany przez Skaner Doradca (backup)",
              f"-- {dt.datetime.now().isoformat(timespec='seconds')}", ""]
    for t in tabele:
        linie = []
        for k in _kolumny(t):
            if (t, k["column_name"]) in KOLUMNY_POMIJANE:
                continue
            if str(k.get("is_generated", "NEVER")).upper() == "ALWAYS":
                continue
            typ = k["data_type"]
            if typ == "character varying":
                typ = "text"
            df = f" DEFAULT {k['column_default']}" if k["column_default"] else ""
            nn = "" if k["is_nullable"] == "YES" else " NOT NULL"
            linie.append(f"    {k['column_name']} {typ}{df}{nn}")
        czesci.append(f"CREATE TABLE IF NOT EXISTS {t} (\n" +
                      ",\n".join(linie) + "\n);\n")
    return "\n".join(czesci)


# ---------------------------------------------------------------------------
# EKSPORT DANYCH
# ---------------------------------------------------------------------------
def _eksport_csv(tabela: str, bez_tekstow: bool) -> tuple[str, int]:
    """Zwraca (csv_string, liczba_wierszy)."""
    kolumny = [k["column_name"] for k in _kolumny(tabela)
               if (tabela, k["column_name"]) not in KOLUMNY_POMIJANE
               and str(k.get("is_generated", "NEVER")).upper() != "ALWAYS"]
    wybor = list(kolumny)
    if bez_tekstow and tabela == "dokumenty" and "tekst" in wybor:
        # zamiast pełnego tekstu — pusty placeholder (kolumna zostaje w CSV,
        # żeby import nie wymagał zmiany struktury)
        wybor[wybor.index("tekst")] = "'' AS tekst"
    rows = _zapytaj(f"SELECT {', '.join(wybor)} FROM {tabela}")
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=kolumny, quoting=csv.QUOTE_ALL)
    w.writeheader()
    for r in rows:
        w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in kolumny})
    return buf.getvalue(), len(rows)


_PRZYWRACANIE = """PRZYWRACANIE BACKUPU — Skaner Doradca
=====================================================
1. Utwórz pustą bazę (np. nowy projekt Supabase albo lokalny PostgreSQL).
2. W SQL Editorze uruchom całą zawartość pliku schema.sql.
3. Zaimportuj pliki CSV do odpowiadających im tabel:
   - Supabase: Table Editor -> tabela -> Import data via CSV,
   - psql:  \\copy nazwa_tabeli FROM 'nazwa_tabeli.csv' CSV HEADER;
4. Kolejność importu jest dowolna (schemat nie używa kluczy obcych).
5. Jeżeli backup był w wariancie "bez pełnych tekstów", kolumna
   dokumenty.tekst jest pusta — pełne treści można odtworzyć ponownym
   pobraniem z API Eureka (Ściągacz / synchronizacja).
6. Kolumna dokumenty.tekst_search (generowana) odtworzy się automatycznie,
   jeżeli po imporcie dodasz ją poleceniem z oryginalnej konfiguracji.
"""


def _zbuduj_zip(bez_tekstow: bool, pasek) -> tuple[bytes, list[str]]:
    tabele = _lista_tabel()
    raport = []
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("schema.sql", _schema_sql(tabele))
        z.writestr("PRZYWRACANIE.txt", _PRZYWRACANIE)
        for i, t in enumerate(tabele, start=1):
            pasek.progress(i / max(len(tabele), 1),
                           text=f"Eksportuję: {t} ({i}/{len(tabele)})")
            csv_txt, n = _eksport_csv(t, bez_tekstow)
            z.writestr(f"{t}.csv", csv_txt)
            raport.append(f"{t}: {n} wierszy")
    return buf.getvalue(), raport


# ---------------------------------------------------------------------------
# WEJŚCIE
# ---------------------------------------------------------------------------
# ZARZĄDZANIE KONTAMI (tylko admin)
# ---------------------------------------------------------------------------
def _wyslij_kod(email: str, kod: str, cel: str) -> tuple[bool, str]:
    """Wysyła kod na adres konta przez SMTP z sekcji [smtp] Streamlit Secrets.
    Zwraca (sukces, komunikat_bledu)."""
    try:
        cfg = st.secrets["smtp"]
        host, port = cfg["host"], int(cfg.get("port", 587))
        user, haslo = cfg["user"], cfg["password"]
        nadawca = cfg.get("from", user)
    except Exception:
        return False, "Brak lub niepełna sekcja [smtp] w Secrets."

    tresc = (
        f"Cześć,\n\n{cel} dla konta {email} w systemie Skaner Doradca.\n\n"
        f"Twój kod: {kod}\n\n"
        f"Kod jest ważny 24 godziny. Wpisz go w aplikacji w zakładce "
        f"„Pierwsze logowanie / aktywacja” razem z adresem e-mail i nowym "
        f"hasłem (min. 8 znaków, w tym cyfra i znak specjalny).\n\n"
        f"— Skaner Doradca"
    )
    msg = MIMEText(tresc, "plain", "utf-8")
    msg["Subject"] = "Aktywacja konta — Skaner Doradca"
    msg["From"] = formataddr(("Skaner Doradca", nadawca))
    msg["To"] = email
    try:
        with smtplib.SMTP(host, port, timeout=30) as s:
            s.starttls()
            s.login(user, haslo)
            s.sendmail(nadawca, [email], msg.as_string())
        return True, ""
    except Exception as e:
        return False, str(e)


def _po_wygenerowaniu_kodu(email: str, kod: str, cel: str) -> None:
    """Wysyła kod mailem; gdy się nie uda — pokazuje go adminowi na ekranie."""
    ok, blad = _wyslij_kod(email, kod, cel)
    if ok:
        st.success(f"Kod wysłany na {email}. Ważny 24 h.")
    else:
        st.warning(
            f"Nie udało się wysłać maila ({blad}). Przekaż użytkownikowi "
            f"kod ręcznie — jest ważny 24 h:"
        )
        st.code(kod, language=None)


def _panel_kont() -> None:
    # Widoczny wyłącznie dla administratora (i konta zaszytego DORADCA).
    rola = st.session_state.get("rola")
    if not auth.ma_uprawnienie(rola, "zarzadzanie_kontami"):
        return

    st.subheader("Zarządzanie kontami")
    try:
        auth.zapewnij_tabele()
    except Exception as e:
        st.error(f"Nie udało się przygotować tabeli kont: {e}")
        return

    # ── Tworzenie konta ─────────────────────────────────────────────────────
    with st.expander("➕ Utwórz nowe konto", expanded=False):
        c1, c2 = st.columns([3, 1])
        with c1:
            nowy_email = st.text_input(
                f"Adres e-mail ({auth.DOMENA})", key="kont_email")
        with c2:
            etykieta_rola = st.selectbox(
                "Rola", ["Użytkownik", "Administrator"], key="kont_rola")
        if st.button("Utwórz konto i wyślij kod", type="primary", key="kont_utworz"):
            rola_val = "admin" if etykieta_rola == "Administrator" else "user"
            try:
                kod = auth.utworz_konto(nowy_email.strip().lower(), rola_val)
                st.success(f"Konto utworzone: {nowy_email.strip().lower()} "
                           f"({etykieta_rola}).")
                _po_wygenerowaniu_kodu(nowy_email.strip().lower(), kod,
                                       "Utworzono konto")
            except ValueError as e:
                st.error(str(e))
            except Exception as e:
                st.error(f"Nie udało się utworzyć konta: {e}")

    # ── Lista kont ──────────────────────────────────────────────────────────
    try:
        konta = auth.lista_uzytkownikow()
    except Exception as e:
        st.error(f"Nie udało się pobrać listy kont: {e}")
        return

    st.caption("Konto administracyjne „DORADCA” jest zaszyte i nie figuruje "
               "na tej liście (awaryjny dostęp).")
    if not konta:
        st.info("Brak kont bazodanowych. Utwórz pierwsze powyżej.")
        return

    _ETYKIETY_STATUS = {"oczekuje": "⏳ oczekuje na aktywację",
                        "aktywne": "✅ aktywne", "nieaktywne": "⛔ nieaktywne"}
    for u in konta:
        c1, c2, c3, c4 = st.columns([3, 2, 2, 2])
        c1.markdown(f"**{u['email']}**")
        c2.markdown("Administrator" if u["rola"] == "admin" else "Użytkownik")
        c3.markdown(_ETYKIETY_STATUS.get(u["status"], u["status"]))
        with c4:
            if u["status"] != "nieaktywne":
                if st.button("Reset hasła", key=f"kont_reset_{u['id']}",
                             help="Wygeneruj nowy kod; użytkownik ustawi hasło "
                                  "od nowa przez aktywację"):
                    try:
                        kod = auth.wygeneruj_nowy_kod(u["email"])
                        _po_wygenerowaniu_kodu(u["email"], kod, "Reset hasła")
                    except Exception as e:
                        st.error(f"Nie udało się: {e}")
                if st.button("Dezaktywuj", key=f"kont_deakt_{u['id']}",
                             help="Konto nie będzie mogło się logować "
                                  "(dane i alerty zostają)"):
                    auth.dezaktywuj(u["email"])
                    st.rerun()
            else:
                if st.button("Aktywuj ponownie", key=f"kont_reakt_{u['id']}",
                             help="Odblokuj konto (jeśli ma ustawione hasło)"):
                    auth.aktywuj_ponownie(u["email"])
                    st.rerun()


# ---------------------------------------------------------------------------
def pokaz_ustawienia() -> None:
    st.header("🛠️ Ustawienia Systemu")

    _panel_kont()
    st.divider()

    st.subheader("Backup bazy danych")
    st.caption(
        "Jeden klik: eksport wszystkich tabel do archiwum ZIP "
        "(CSV + schemat + instrukcja odtworzenia). Plik zapisuje się "
        "na Twoim komputerze — nic nie zostaje na serwerze."
    )

    bez_tekstow = st.toggle(
        "Backup bez pełnych tekstów interpretacji (szybszy, dużo mniejszy)",
        value=False,
        help="Pomija zawartość dokumenty.tekst — jedyne dane w pełni "
             "odtwarzalne ponownym pobraniem z API Eureka. Cała reszta "
             "(streszczenia, klasyfikacje, subskrypcje, historia) jest "
             "eksportowana zawsze w całości.",
    )

    if st.button("📦 Przygotuj backup", type="primary"):
        pasek = st.progress(0.0, text="Startuję…")
        try:
            dane, raport = _zbuduj_zip(bez_tekstow, pasek)
        except Exception as e:
            pasek.empty()
            st.error(f"Backup nie powiódł się: {e}")
            return
        pasek.empty()
        znacznik = dt.datetime.now().strftime("%Y-%m-%d_%H%M")
        wariant = "_bez_tekstow" if bez_tekstow else "_pelny"
        st.session_state["backup_dane"] = dane
        st.session_state["backup_nazwa"] = f"skaner_backup{wariant}_{znacznik}.zip"
        st.session_state["backup_raport"] = raport

    if st.session_state.get("backup_dane"):
        rozmiar_mb = len(st.session_state["backup_dane"]) / (1024 * 1024)
        st.success(
            f"Backup gotowy: **{st.session_state['backup_nazwa']}** "
            f"({rozmiar_mb:.1f} MB)"
        )
        st.download_button(
            "⬇️ Pobierz backup",
            data=st.session_state["backup_dane"],
            file_name=st.session_state["backup_nazwa"],
            mime="application/zip",
            type="primary",
            use_container_width=True,
        )
        with st.expander("Co jest w środku"):
            for linia in st.session_state.get("backup_raport", []):
                st.caption(linia)
            st.caption("+ schema.sql (struktury tabel) + PRZYWRACANIE.txt")

    st.divider()
    st.caption(
        "Pozostałe ustawienia systemu pojawią się w przyszłych wersjach."
    )


if __name__ == "__main__":
    st.set_page_config(page_title="Ustawienia Systemu", layout="wide")
    pokaz_ustawienia()
