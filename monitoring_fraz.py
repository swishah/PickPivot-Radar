# -*- coding: utf-8 -*-
"""
monitoring_fraz.py — MONITORING FRAZ + POWIADOMIENIA E-MAIL (GitHub Actions).
Niezależny od Streamlit. Konfiguracja z os.environ.

Co robi przy każdym uruchomieniu:
  1. Pobiera aktywne obserwowane frazy (tabela obserwowane_frazy — dodawane
     w aplikacji, moduł „Monitoring Fraz”).
  2. Szuka dopasowań w dokumentach pobranych w ostatnich OKNO_DNI dniach
     (ILIKE na treści i sygnaturze — łapie odmiany: „ciepłownictw” trafi
     „ciepłownictwa”, „ciepłownictwem” itd.).
  3. Pomija pary (fraza, dokument), o których już wysłano powiadomienie
     (tabela monitoring_wyslane) — IDEMPOTENTNY, można odpalać wielokrotnie.
  4. Grupuje nowe trafienia per adres e-mail i wysyła JEDEN zbiorczy mail
     na adres, po czym zapisuje pary jako wysłane.

Zmienne środowiskowe (GitHub Secrets):
  SUPABASE_DB_URL lub SUPABASE_HOST/USER/PASSWORD[/PORT/DB]  — jak w automacie
  SMTP_HOST      — np. smtp.gmail.com
  SMTP_PORT      — np. 587 (STARTTLS)
  SMTP_USER      — login SMTP (np. adres Gmail)
  SMTP_PASSWORD  — hasło SMTP (dla Gmaila: HASŁO APLIKACJI, nie zwykłe)
  SMTP_FROM      — opcjonalnie nadawca (domyślnie SMTP_USER)
Opcjonalne:
  MONITORING_OKNO_DNI — ile dni wstecz po pobrano_at (domyślnie 3)
"""

from __future__ import annotations

import os
import smtplib
import sys
from email.mime.text import MIMEText
from email.utils import formataddr

import db_core

OKNO_DNI = int(os.environ.get("MONITORING_OKNO_DNI") or "3")


# ---------------------------------------------------------------------------
def _polacz() -> db_core.SupabaseDB:
    url = os.environ.get("SUPABASE_DB_URL")
    if url:
        return db_core.SupabaseDB({"url": url})
    braki = [k for k in ("SUPABASE_HOST", "SUPABASE_USER", "SUPABASE_PASSWORD")
             if not os.environ.get(k)]
    if braki:
        raise SystemExit("Brak konfiguracji bazy: " + ", ".join(braki))
    return db_core.SupabaseDB({
        "host": os.environ["SUPABASE_HOST"],
        "port": os.environ.get("SUPABASE_PORT") or "5432",
        "database": os.environ.get("SUPABASE_DB") or "postgres",
        "user": os.environ["SUPABASE_USER"],
        "password": os.environ["SUPABASE_PASSWORD"],
    })


def zapewnij_tabele(db: db_core.SupabaseDB) -> None:
    """Wywoływane też przez moduł Streamlit (wspólny schemat)."""
    db.wykonaj(
        """
        CREATE TABLE IF NOT EXISTS obserwowane_frazy (
            id        SERIAL PRIMARY KEY,
            fraza     TEXT NOT NULL,
            email     TEXT NOT NULL,
            podatek   TEXT DEFAULT '',      -- '' = wszystkie podatki
            aktywna   BOOLEAN DEFAULT TRUE,
            utworzono TEXT NOT NULL,
            UNIQUE (fraza, email, podatek)
        )
        """
    )
    db.wykonaj(
        """
        CREATE TABLE IF NOT EXISTS monitoring_wyslane (
            id          SERIAL PRIMARY KEY,
            fraza_id    INTEGER NOT NULL,
            dokument_id TEXT NOT NULL,
            wyslano     TEXT NOT NULL,
            UNIQUE (fraza_id, dokument_id)
        )
        """
    )
    db.wykonaj(
        """
        CREATE TABLE IF NOT EXISTS obserwowane_branze (
            id        SERIAL PRIMARY KEY,
            branza    TEXT NOT NULL,      -- wartość z taksonomii BRANZE
            email     TEXT NOT NULL,
            aktywna   BOOLEAN DEFAULT TRUE,
            utworzono TEXT NOT NULL,
            UNIQUE (branza, email)
        )
        """
    )
    db.wykonaj(
        """
        CREATE TABLE IF NOT EXISTS monitoring_branze_wyslane (
            id          SERIAL PRIMARY KEY,
            sub_id      INTEGER NOT NULL,
            dokument_id TEXT NOT NULL,
            wyslano     TEXT NOT NULL,
            UNIQUE (sub_id, dokument_id)
        )
        """
    )


# ---------------------------------------------------------------------------
def _trafienia(db: db_core.SupabaseDB) -> list[dict]:
    """Nowe pary (fraza, dokument): dopasowane, jeszcze nie wysłane."""
    return db.wykonaj(
        f"""
        SELECT f.id AS fraza_id, f.fraza, f.email, f.podatek AS fraza_podatek,
               d.id AS dokument_id, d.podatek, d.sygnatura, d.data_wyd, d.link
        FROM obserwowane_frazy f
        JOIN dokumenty d
          ON (f.podatek = '' OR f.podatek = d.podatek)
         AND (d.tekst ILIKE '%%' || f.fraza || '%%'
              OR d.sygnatura ILIKE '%%' || f.fraza || '%%')
        WHERE f.aktywna = TRUE
          AND d.pobrano_at >= now() - interval '{OKNO_DNI} days'
          AND NOT EXISTS (
                SELECT 1 FROM monitoring_wyslane w
                WHERE w.fraza_id = f.id AND w.dokument_id = d.id)
        ORDER BY f.email, f.fraza, d.data_wyd
        """,
        fetch=True,
    )


def _oznacz_wyslane(db: db_core.SupabaseDB, pary: list[dict]) -> None:
    import datetime as dt
    teraz = dt.datetime.now().isoformat(timespec="seconds")
    for p in pary:
        db.wykonaj(
            """INSERT INTO monitoring_wyslane (fraza_id, dokument_id, wyslano)
               VALUES (%s,%s,%s) ON CONFLICT (fraza_id, dokument_id) DO NOTHING""",
            (p["fraza_id"], p["dokument_id"], teraz),
        )


# ---------------------------------------------------------------------------
def _tresc_maila(trafienia: list[dict]) -> str:
    linie = ["Nowe interpretacje pasujące do obserwowanych fraz",
             "(Skaner Doradca — monitoring fraz)", ""]
    wg_frazy: dict[str, list[dict]] = {}
    for t in trafienia:
        wg_frazy.setdefault(t["fraza"], []).append(t)
    for fraza, lista in wg_frazy.items():
        linie.append(f"■ Fraza: „{fraza}” — trafień: {len(lista)}")
        for t in lista:
            data = str(t["data_wyd"])[:10]
            linie.append(f"   • [{t['podatek']}] {t['sygnatura']} "
                         f"(wydana {data})")
            if t.get("link"):
                linie.append(f"     {t['link']}")
        linie.append("")
    linie.append("— Wiadomość wygenerowana automatycznie. Frazy zarządzasz "
                 "w aplikacji, moduł „Monitoring Fraz”.")
    return "\n".join(linie)


def _wyslij_temat(adres: str, tresc: str, temat: str) -> None:
    host = os.environ.get("SMTP_HOST")
    port = int(os.environ.get("SMTP_PORT") or "587")
    user = os.environ.get("SMTP_USER")
    haslo = os.environ.get("SMTP_PASSWORD")
    if not (host and user and haslo):
        raise SystemExit("Brak konfiguracji SMTP (SMTP_HOST/SMTP_USER/SMTP_PASSWORD).")
    nadawca = os.environ.get("SMTP_FROM") or user

    msg = MIMEText(tresc, "plain", "utf-8")
    msg["Subject"] = temat
    msg["From"] = formataddr(("Skaner Doradca", nadawca))
    msg["To"] = adres

    with smtplib.SMTP(host, port, timeout=30) as s:
        s.starttls()
        s.login(user, haslo)
        s.sendmail(nadawca, [adres], msg.as_string())


def _wyslij(adres: str, tresc: str, ile: int) -> None:
    _wyslij_temat(adres, tresc,
                  f"[Skaner Doradca] Monitoring fraz: {ile} nowych trafień")


def _trafienia_branz(db: db_core.SupabaseDB) -> list[dict]:
    """Nowe pary (subskrypcja branży, dokument): streszczenie z ostatnich
    OKNO_DNI dni ma przypisaną obserwowaną branżę, a powiadomienia jeszcze
    nie wysłano. Branże nadaje model podczas streszczania (klasyfikacja po
    treści, zamknięta taksonomia)."""
    import datetime as dt
    prog = (dt.datetime.now() - dt.timedelta(days=OKNO_DNI)).isoformat(
        timespec="seconds")
    return db.wykonaj(
        """
        SELECT b.id AS sub_id, b.branza, b.email,
               d.id AS dokument_id, d.podatek, d.sygnatura, d.data_wyd, d.link,
               s.temat
        FROM obserwowane_branze b
        JOIN streszczenia_auto s
          ON s.branze ILIKE '%%' || b.branza || '%%'
        JOIN dokumenty d ON d.id = s.dokument_id
        WHERE b.aktywna = TRUE
          AND s.wygenerowano >= %s
          AND NOT EXISTS (
                SELECT 1 FROM monitoring_branze_wyslane w
                WHERE w.sub_id = b.id AND w.dokument_id = d.id)
        ORDER BY b.email, b.branza, d.data_wyd
        """,
        (prog,),
        fetch=True,
    )


def _oznacz_wyslane_branze(db: db_core.SupabaseDB, pary: list[dict]) -> None:
    import datetime as dt
    teraz = dt.datetime.now().isoformat(timespec="seconds")
    for p in pary:
        db.wykonaj(
            """INSERT INTO monitoring_branze_wyslane (sub_id, dokument_id, wyslano)
               VALUES (%s,%s,%s) ON CONFLICT (sub_id, dokument_id) DO NOTHING""",
            (p["sub_id"], p["dokument_id"], teraz),
        )


def _tresc_maila_branze(trafienia: list[dict]) -> str:
    linie = ["Nowe interpretacje z obserwowanych branż",
             "(Skaner Doradca — monitoring branż; klasyfikacja po treści "
             "interpretacji, nadawana automatycznie przy streszczaniu)", ""]
    wg: dict[str, list[dict]] = {}
    for t in trafienia:
        wg.setdefault(t["branza"], []).append(t)
    for branza, lista in wg.items():
        linie.append(f"■ Branża: {branza} — trafień: {len(lista)}")
        for t in lista:
            data = str(t["data_wyd"])[:10]
            linie.append(f"   • [{t['podatek']}] {t['sygnatura']} (wydana {data})")
            if t.get("temat"):
                linie.append(f"     Temat: {t['temat']}")
            if t.get("link"):
                linie.append(f"     {t['link']}")
        linie.append("")
    linie.append("— Wiadomość wygenerowana automatycznie. Branże zarządzasz "
                 "w aplikacji, moduł „Monitoring Branż”.")
    return "\n".join(linie)


# ---------------------------------------------------------------------------
def main() -> int:
    db = _polacz()
    zapewnij_tabele(db)

    # ── kanał 1: frazy ──────────────────────────────────────────────────────
    trafienia = _trafienia(db)
    print(f"[monitoring] Frazy | okno: {OKNO_DNI} dni | nowych trafień: {len(trafienia)}")
    wg_adresu: dict[str, list[dict]] = {}
    for t in trafienia:
        wg_adresu.setdefault(t["email"].strip(), []).append(t)
    for adres, lista in wg_adresu.items():
        try:
            _wyslij(adres, _tresc_maila(lista), len(lista))
            _oznacz_wyslane(db, lista)
            print(f"[monitoring] Frazy → {adres}: {len(lista)} trafień.")
        except SystemExit:
            raise
        except Exception as e:
            print(f"[monitoring] BŁĄD wysyłki (frazy) na {adres}: {e}")

    # ── kanał 2: branże (klasyfikacja modelu przy streszczaniu) ────────────
    tr_b = _trafienia_branz(db)
    print(f"[monitoring] Branże | nowych trafień: {len(tr_b)}")
    wg_adresu_b: dict[str, list[dict]] = {}
    for t in tr_b:
        wg_adresu_b.setdefault(t["email"].strip(), []).append(t)
    for adres, lista in wg_adresu_b.items():
        try:
            _wyslij_temat(adres, _tresc_maila_branze(lista),
                          f"[Skaner Doradca] Monitoring branż: {len(lista)} nowych trafień")
            _oznacz_wyslane_branze(db, lista)
            print(f"[monitoring] Branże → {adres}: {len(lista)} trafień.")
        except SystemExit:
            raise
        except Exception as e:
            print(f"[monitoring] BŁĄD wysyłki (branże) na {adres}: {e}")

    if not trafienia and not tr_b:
        print("[monitoring] Nic do wysłania.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
