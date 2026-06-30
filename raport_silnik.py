"""
raport_silnik.py — Wspolna logika generowania raportu dla DOWOLNEGO zakresu dat i podatku.

Uzywany przez:
  - raport_tygodniowy.py    (cron, caly tydzien, wszystkie podatki)
  - raport_na_zadanie.py    (GitHub Actions workflow_dispatch, jeden rok/miesiac/podatek)
  - raporty.py               (Streamlit, generowanie "na zywo" w aplikacji)

Nie zalezy od Streamlit ani od GitHub Actions - czysta logika biznesowa.
"""

import io
import os
import smtplib
import requests
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from docx import Document

import db_core
import utils


PODATKI_WSZYSTKIE = ["PIT", "CIT", "VAT", "AKCYZA"]


# ---------------------------------------------------------------------------
# GENEROWANIE WORD
# ---------------------------------------------------------------------------
def generuj_word(rekordy: list, podatek: str, opis_okresu: str, tytul_raportu: str = "Raport") -> bytes:
    """
    rekordy: lista slownikow z kluczami Sygnatura/Data/Podatek/Link/Tekst/Format
    opis_okresu: czytelny string np. "Styczen 2026" lub "06.01 — 10.01.2026"
    """
    doc = Document()
    doc.add_heading(f"PickPivot — {tytul_raportu}: {podatek}", 0)
    doc.add_paragraph(f"Okres: {opis_okresu}")
    doc.add_paragraph(f"Wygenerowano: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    doc.add_paragraph(f"Liczba dokumentow: {len(rekordy)}")
    doc.add_page_break()

    for r in sorted(rekordy, key=lambda x: x.get("Data", ""), reverse=True):
        doc.add_heading(f"Sygnatura: {r['Sygnatura']}", 1)
        doc.add_paragraph(f"Data:    {r['Data']}")
        doc.add_paragraph(f"Podatek: {r['Podatek']}")
        doc.add_paragraph(f"Link:    {r['Link']}")
        if r.get("Format"):
            doc.add_paragraph(f"Zrodlo:  {r['Format']}")
        doc.add_paragraph(utils.wyczysc_tekst_dla_worda(r["Tekst"]))
        doc.add_page_break()

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# UZUPELNIANIE ARCHIWUM Z API MF DLA DOWOLNEGO ZAKRESU DAT
# ---------------------------------------------------------------------------
def uzupelnij_archiwum(
    db: db_core.SupabaseDB,
    podatek: str,
    data_od: datetime,
    data_do: datetime,
    log_fn=print,
    workers: int = 4,
) -> tuple:
    """
    Sprawdza API MF dla okresu i pobiera brakujace dokumenty do archiwum.
    Zwraca (liczba_nowych, status: "OK"|"BLOKADA"|"ERROR").
    """
    log_fn(f"[{podatek}] Sprawdzam API MF dla okresu {data_od.date()} — {data_do.date()}...")

    znane_id = db_core.pobierz_id_z_archiwum(db)

    with requests.Session() as sesja:
        lista, status = utils.pobierz_wszystko_z_okresu(
            data_od.strftime("%Y-%m-%d"),
            data_do.strftime("%Y-%m-%d"),
            sesja, podatek, utils.KODY_PODATKOW[podatek],
        )

    if status != "OK":
        log_fn(f"[{podatek}] OSTRZEZENIE: API MF zwrocilo status {status}")
        return 0, status

    do_pobrania = [d for d in lista if d["id"] not in znane_id]
    log_fn(f"[{podatek}] Znaleziono {len(lista)} w MF, do pobrania: {len(do_pobrania)}")

    if not do_pobrania:
        log_fn(f"[{podatek}] Archiwum juz aktualne dla tego okresu.")
        return 0, "OK"

    def on_postep(completed, total, sygnatura, status):
        if completed % 10 == 0 or completed == total:
            log_fn(f"[{podatek}] Postep: {completed}/{total}")

    nowe_tresci, _, _, blokada = utils.pobierz_dokumenty_rownolegle(
        do_pobrania, znane_id, set(),
        callback_postep=on_postep, workers=workers,
    )

    if nowe_tresci:
        zapisanych = db_core.zapisz_wiele_do_archiwum(db, nowe_tresci, "raport_na_zadanie")
        log_fn(f"[{podatek}] Zapisano {zapisanych} nowych dokumentow.")
        return zapisanych, ("BLOKADA" if blokada else "OK")

    return 0, ("BLOKADA" if blokada else "OK")


# ---------------------------------------------------------------------------
# GENEROWANIE RAPORTU DLA JEDNEGO PODATKU + ZAKRESU DAT (rdzen logiki)
# ---------------------------------------------------------------------------
def generuj_raport_dla_podatku(
    db: db_core.SupabaseDB,
    podatek: str,
    data_od: datetime,
    data_do: datetime,
    opis_okresu: str,
    log_fn=print,
) -> dict:
    """
    Pelny przebieg dla JEDNEGO podatku: uzupelnia archiwum, generuje Word, zwraca wynik.

    Zwraca dict:
        {
            "podatek": str, "liczba_dok": int, "plik_bytes": bytes|None,
            "nowych_pobranych": int, "status": "OK"|"BLOKADA"|"ERROR"|"BRAK_DOKUMENTOW"
        }
    """
    try:
        nowych, status_pobierania = uzupelnij_archiwum(db, podatek, data_od, data_do, log_fn=log_fn)

        rekordy = db_core.pobierz_rekordy_z_archiwum(
            db, podatek=podatek,
            data_od=data_od.strftime("%Y-%m-%d"),
            data_do=data_do.strftime("%Y-%m-%d"),
        )

        if not rekordy:
            return {
                "podatek": podatek, "liczba_dok": 0, "plik_bytes": None,
                "nowych_pobranych": nowych, "status": "BRAK_DOKUMENTOW",
            }

        plik_bytes = generuj_word(rekordy, podatek, opis_okresu, tytul_raportu="Raport na zadanie")

        return {
            "podatek": podatek, "liczba_dok": len(rekordy), "plik_bytes": plik_bytes,
            "nowych_pobranych": nowych, "status": status_pobierania,
        }

    except Exception as e:
        log_fn(f"[{podatek}] BLAD: {e}")
        return {
            "podatek": podatek, "liczba_dok": 0, "plik_bytes": None,
            "nowych_pobranych": 0, "status": "ERROR", "error_msg": str(e),
        }


# ---------------------------------------------------------------------------
# WYSYLKA EMAIL Z ZALACZNIKIEM
# ---------------------------------------------------------------------------
def wyslij_email_z_zalacznikiem(
    plik_bytes: bytes,
    nazwa_pliku: str,
    podatek: str,
    opis_okresu: str,
    liczba_dok: int,
    gmail_adres: str,
    gmail_haslo: str,
    odbiorca: str,
    log_fn=print,
) -> bool:
    """Wysyla e-mail z plikiem Word jako zalacznik. Zwraca True przy sukcesie."""
    from email.mime.application import MIMEApplication

    temat = f"PickPivot — Raport na zadanie: {podatek} ({opis_okresu})"

    tresc_html = f"""
    <html><body style="font-family: Arial, sans-serif;">
    <h2>📄 PickPivot — Raport na żądanie</h2>
    <p><b>Podatek:</b> {podatek}</p>
    <p><b>Okres:</b> {opis_okresu}</p>
    <p><b>Liczba dokumentów:</b> {liczba_dok}</p>
    <p style="margin-top: 20px;">Plik Word w załączniku.</p>
    <p style="color: #888; font-size: 12px;">
        Wiadomość wygenerowana na żądanie użytkownika PickPivot.
    </p>
    </body></html>
    """

    msg = MIMEMultipart("mixed")
    msg["Subject"] = temat
    msg["From"]    = gmail_adres
    msg["To"]      = odbiorca

    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(tresc_html, "html", "utf-8"))
    msg.attach(alt)

    zalacznik = MIMEApplication(plik_bytes, _subtype="vnd.openxmlformats-officedocument.wordprocessingml.document")
    zalacznik.add_header("Content-Disposition", "attachment", filename=nazwa_pliku)
    msg.attach(zalacznik)

    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()
            server.login(gmail_adres, gmail_haslo)
            server.send_message(msg)
        log_fn(f"E-mail z raportem ({podatek}) wyslany do {odbiorca}.")
        return True
    except Exception as e:
        log_fn(f"BLAD wysylki email: {e}")
        return False


def wyslij_email_podsumowanie_wielu(
    wyniki: list,
    opis_okresu: str,
    gmail_adres: str,
    gmail_haslo: str,
    odbiorca: str,
    log_fn=print,
) -> bool:
    """
    Wysyla JEDEN email podsumowujacy gdy generowano kilka podatkow naraz,
    z wszystkimi plikami jako zalaczniki.
    wyniki: lista dict z generuj_raport_dla_podatku()
    """
    from email.mime.application import MIMEApplication

    podatki_str = "/".join(w["podatek"] for w in wyniki)
    temat = f"PickPivot — Raport na zadanie: {podatki_str} ({opis_okresu})"

    wiersze = "".join(
        f'<tr><td>{w["podatek"]}</td><td>{w["liczba_dok"]}</td>'
        f'<td>{"✅ Zalaczony" if w["plik_bytes"] else "— brak dokumentow"}</td></tr>'
        for w in wyniki
    )

    tresc_html = f"""
    <html><body style="font-family: Arial, sans-serif;">
    <h2>📄 PickPivot — Raport na żądanie</h2>
    <p><b>Okres:</b> {opis_okresu}</p>
    <table border="1" cellpadding="8" cellspacing="0" style="border-collapse: collapse;">
        <tr style="background-color: #2C3E50; color: white;">
            <th>Podatek</th><th>Liczba dokumentów</th><th>Plik</th>
        </tr>
        {wiersze}
    </table>
    <p style="margin-top: 20px;">Pliki Word w załączniku (jeśli były dokumenty).</p>
    <p style="color: #888; font-size: 12px;">
        Wiadomość wygenerowana na żądanie użytkownika PickPivot.
    </p>
    </body></html>
    """

    msg = MIMEMultipart("mixed")
    msg["Subject"] = temat
    msg["From"]    = gmail_adres
    msg["To"]      = odbiorca

    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(tresc_html, "html", "utf-8"))
    msg.attach(alt)

    for w in wyniki:
        if w["plik_bytes"]:
            nazwa = f"Raport_{w['podatek']}_{opis_okresu.replace(' ', '_').replace('—','-')}.docx"
            zalacznik = MIMEApplication(
                w["plik_bytes"],
                _subtype="vnd.openxmlformats-officedocument.wordprocessingml.document"
            )
            zalacznik.add_header("Content-Disposition", "attachment", filename=nazwa)
            msg.attach(zalacznik)

    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()
            server.login(gmail_adres, gmail_haslo)
            server.send_message(msg)
        log_fn(f"E-mail podsumowujacy wyslany do {odbiorca}.")
        return True
    except Exception as e:
        log_fn(f"BLAD wysylki email: {e}")
        return False


# ---------------------------------------------------------------------------
# POMOCNICZE — zakres dat z roku/miesiaca
# ---------------------------------------------------------------------------
def zakres_z_roku_miesiaca(rok: int, miesiac: int) -> tuple:
    """Zwraca (data_od, data_do, opis_okresu) dla calego miesiaca."""
    import calendar as cal_mod
    data_od = datetime(rok, miesiac, 1)
    ostatni_dzien = cal_mod.monthrange(rok, miesiac)[1]
    data_do = datetime(rok, miesiac, ostatni_dzien)

    miesiace_pl = ["Styczen","Luty","Marzec","Kwiecien","Maj","Czerwiec",
                   "Lipiec","Sierpien","Wrzesien","Pazdziernik","Listopad","Grudzien"]
    opis = f"{miesiace_pl[miesiac-1]} {rok}"
    return data_od, data_do, opis
