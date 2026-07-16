# -*- coding: utf-8 -*-
"""
streszczacz_openrouter.py — Klient OpenRouter do streszczania interpretacji.
NIEZALEŻNY od Streamlit (można go użyć też w skrypcie GitHub Actions).

API OpenRouter jest zgodne z OpenAI: POST na /chat/completions z nagłówkiem
Authorization: Bearer <klucz>. Domyślny model to auto-router "openrouter/free",
który sam dobiera dostępny darmowy model — odporny na rotację darmowej oferty.

Klucz API przekazuje się z zewnątrz (z st.secrets w Streamlit albo z os.environ
w GitHub Actions) — ten moduł go nie czyta samodzielnie.
"""

from __future__ import annotations

import json
import re
import time

import requests

ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"

# Auto-router darmowych modeli — przeżywa rotację (gdy konkretny :free znika).
MODEL_DOMYSLNY = "openrouter/free"

MODELE_DO_WYBORU = [
    "openrouter/free",
    "meta-llama/llama-4-maverick:free",
    "meta-llama/llama-4-scout:free",
    "qwen/qwen3-235b-a22b:free",
    "openai/gpt-oss-120b:free",
]

# Wystarczająco dużo, by 12-zdaniowe polskie streszczenie NIE zostało ucięte
# (polski jest „cięższy” tokenowo niż angielski — przy 1200 tokenach odpowiedzi
#  bywały urywane w połowie zdania, co psuło JSON).
MAKS_TOKENOW = 2200

_SYSTEM = (
    "Jesteś asystentem polskiego doradcy podatkowego. Streszczasz polskie "
    "interpretacje indywidualne WIERNIE, wyłącznie na podstawie dostarczonej "
    "treści. Nie zmyślasz, nie dodajesz wiedzy spoza tekstu, nie oceniasz.\n\n"
    "BEZWZGLĘDNY WYMÓG JĘZYKA: całość — i temat, i streszczenie — MUSI być "
    "napisana PO POLSKU. Nawet jeśli fragmenty źródła są po angielsku, "
    "streszczasz PO POLSKU. Odpowiedź w innym języku niż polski jest błędem.\n\n"
    "Zwróć WYŁĄCZNIE obiekt JSON (bez komentarzy, bez bloków ```), o kluczach:\n"
    '  "temat"        — jedno zwięzłe zdanie po polsku: czego dotyczy '
    "interpretacja,\n"
    '  "streszczenie" — ciągła proza po polsku (NIE lista), maksymalnie 12 '
    "zdań, ujmująca: stan faktyczny lub zdarzenie przyszłe, pytanie "
    "wnioskodawcy, jego stanowisko oraz stanowisko organu z kluczowym "
    "uzasadnieniem."
)


# ---------------------------------------------------------------------------
# PARSOWANIE ODPOWIEDZI — odporne na ucięty / niedomknięty JSON
# ---------------------------------------------------------------------------
def _odkoduj(val: str) -> str:
    for a, b in (('\\"', '"'), ("\\n", "\n"), ("\\t", "\t"), ("\\/", "/")):
        val = val.replace(a, b)
    return val.strip().rstrip('"').strip()


def _wytnij_pole(t: str, klucz: str) -> str:
    """Wyciąga wartość pola z (także niekompletnego) JSON-a.
    Najpierw poprawnie zamknięty string, a gdy odpowiedź jest ucięta —
    bierze wszystko od otwierającego cudzysłowu do końca."""
    m = re.search(r'"' + klucz + r'"\s*:\s*"((?:[^"\\]|\\.)*)"', t, re.S)
    if not m:
        m = re.search(r'"' + klucz + r'"\s*:\s*"(.*)$', t, re.S)
    return _odkoduj(m.group(1)) if m else ""


def _wyodrebnij_json(tresc: str) -> dict:
    t = tresc.strip()
    if t.startswith("```"):
        t = t.strip("`")
        if t[:4].lower() == "json":
            t = t[4:]
        t = t.strip()

    # 1) próba pełnego, poprawnego JSON-a
    i, j = t.find("{"), t.rfind("}")
    if i != -1 and j != -1 and j > i:
        try:
            d = json.loads(t[i:j + 1])
            return {"temat": str(d.get("temat", "")).strip(),
                    "streszczenie": str(d.get("streszczenie", "")).strip()}
        except Exception:
            pass

    # 2) ratunkowa ekstrakcja pól (obsługuje ucięty JSON — bez rusztowania)
    temat = _wytnij_pole(t, "temat")
    streszcz = _wytnij_pole(t, "streszczenie")
    if streszcz:
        return {"temat": temat, "streszczenie": streszcz}

    # 3) ostatecznie: cała treść jako streszczenie, ale BEZ nagłówków JSON
    czysty = re.sub(r'^\s*\{?\s*"?(temat|streszczenie)"?\s*:\s*"?', "", t)
    return {"temat": temat, "streszczenie": _odkoduj(czysty)}


# ---------------------------------------------------------------------------
# WYKRYWANIE ODPOWIEDZI PO ANGIELSKU
# ---------------------------------------------------------------------------
_POLSKIE_ZNAKI = set("ąćęłńóśźżĄĆĘŁŃÓŚŹŻ")
_ANG_SLOWA = ("the", "and", "of", "is", "are", "that", "this", "which", "for",
              "with", "applicant", "authority", "company", "tax", "costs",
              "income", "court", "provided", "concerning")


def _po_angielsku(t: str) -> bool:
    """Heurystyka: dłuższy tekst BEZ ani jednego polskiego znaku diakrytycznego,
    zawierający kilka angielskich słów funkcyjnych, to praktycznie na pewno
    odpowiedź po angielsku (12-zdaniowe polskie streszczenie prawne bez żadnego
    ą/ć/ę/ł/… nie występuje)."""
    if not t or len(t.strip()) < 120:
        return False
    if any(ch in _POLSKIE_ZNAKI for ch in t):
        return False
    low = " " + t.lower() + " "
    trafienia = sum(1 for w in _ANG_SLOWA if (" " + w + " ") in low)
    return trafienia >= 3


# ---------------------------------------------------------------------------
# GŁÓWNA FUNKCJA
# ---------------------------------------------------------------------------
def streszcz_tekst(
    tekst: str,
    sygnatura: str,
    data_wyd: str,
    *,
    api_key: str,
    model: str = MODEL_DOMYSLNY,
    limit_znakow: int = 20000,
    timeout: int = 90,
    proby: int = 3,
) -> dict:
    """Zwraca {"temat": ..., "streszczenie": ...} PO POLSKU.
    Rzuca RuntimeError po wyczerpaniu prób (limit 429, brak odpowiedzi
    albo uporczywa odpowiedź po angielsku)."""
    if not api_key:
        raise RuntimeError("Brak klucza API OpenRouter.")

    tresc_wejscia = (tekst or "")[:limit_znakow]
    user_bazowy = (f"Sygnatura: {sygnatura}\nData wydania: {data_wyd}\n\n"
                   f"TREŚĆ INTERPRETACJI:\n{tresc_wejscia}")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://skaner-doradca.streamlit.app",
        "X-Title": "Skaner Doradca",
    }

    ostatni_blad = None
    dopisek_pl = ""  # wzmacniany, gdy model odpowie po angielsku
    for i in range(proby):
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": user_bazowy + dopisek_pl},
            ],
            "temperature": 0.2,
            "max_tokens": MAKS_TOKENOW,
        }
        try:
            r = requests.post(ENDPOINT, headers=headers, json=payload, timeout=timeout)
        except requests.RequestException as e:
            ostatni_blad = f"Błąd sieci: {e}"
            time.sleep(3 * (i + 1))
            continue

        if r.status_code == 429:
            ostatni_blad = "Przekroczony limit zapytań (429)."
            time.sleep(6 * (i + 1))
            continue
        if r.status_code >= 400:
            try:
                info = r.json().get("error", {}).get("message", r.text[:200])
            except Exception:
                info = r.text[:200]
            ostatni_blad = f"HTTP {r.status_code}: {info}"
            if r.status_code in (401, 402, 403):
                raise RuntimeError(ostatni_blad)
            time.sleep(4 * (i + 1))
            continue

        try:
            dane = r.json()
            tresc = dane["choices"][0]["message"]["content"]
        except Exception as e:
            ostatni_blad = f"Nieoczekiwana odpowiedź: {e}"
            time.sleep(3 * (i + 1))
            continue

        if not tresc or not tresc.strip():
            ostatni_blad = "Pusta odpowiedź modelu."
            time.sleep(3 * (i + 1))
            continue

        wynik = _wyodrebnij_json(tresc)

        # Kontrola języka — jeśli po angielsku, wzmocnij instrukcję i ponów.
        if _po_angielsku(wynik.get("streszczenie", "")):
            ostatni_blad = "Model odpowiedział po angielsku."
            if i < proby - 1:
                dopisek_pl = (
                    "\n\n[PRZYPOMNIENIE] Odpowiedz WYŁĄCZNIE PO POLSKU. "
                    "Temat i streszczenie po polsku. Nie używaj języka angielskiego."
                )
                continue
            raise RuntimeError(
                "Model uporczywie odpowiada po angielsku mimo instrukcji — "
                "zmień model (np. na openrouter/free) i spróbuj ponownie."
            )

        return wynik

    raise RuntimeError(ostatni_blad or "Nie udało się uzyskać streszczenia.")
