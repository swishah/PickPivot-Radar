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
import time

import requests

ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"

# Auto-router darmowych modeli — przeżywa rotację (gdy konkretny :free znika).
MODEL_DOMYSLNY = "openrouter/free"

# Kilka konkretnych darmowych modeli do porównań (dostępność bywa zmienna —
# jeśli któryś zwróci błąd „no endpoints”, wybierz inny lub użyj openrouter/free).
MODELE_DO_WYBORU = [
    "openrouter/free",
    "meta-llama/llama-4-maverick:free",
    "meta-llama/llama-4-scout:free",
    "qwen/qwen3-235b-a22b:free",
    "openai/gpt-oss-120b:free",
]

_SYSTEM = (
    "Jesteś asystentem doradcy podatkowego. Streszczasz polskie interpretacje "
    "indywidualne WIERNIE, wyłącznie na podstawie dostarczonej treści. "
    "Nie zmyślasz, nie dodajesz wiedzy spoza tekstu, nie oceniasz. Jeśli czegoś "
    "nie ma w treści, pomijasz to. Odpowiadasz PO POLSKU.\n\n"
    "Zwróć WYŁĄCZNIE obiekt JSON (bez komentarzy, bez bloków ```), o kluczach:\n"
    '  "temat"        — jedno zwięzłe zdanie: czego dotyczy interpretacja,\n'
    '  "streszczenie" — ciągła proza (NIE lista), maksymalnie 12 zdań, ujmująca: '
    "stan faktyczny lub zdarzenie przyszłe, pytanie wnioskodawcy, jego stanowisko "
    "oraz stanowisko organu wraz z kluczowym uzasadnieniem."
)


def _wyodrebnij_json(tresc: str) -> dict:
    """Parsuje JSON odpornie: zdejmuje ewentualne ```-ogrodzenia i bierze
    fragment od pierwszego { do ostatniego }. Gdy się nie uda — całą treść
    traktuje jako streszczenie (żeby nie stracić wyniku)."""
    t = tresc.strip()
    if t.startswith("```"):
        t = t.strip("`")
        if t[:4].lower() == "json":
            t = t[4:]
    i, j = t.find("{"), t.rfind("}")
    if i != -1 and j != -1 and j > i:
        try:
            d = json.loads(t[i:j + 1])
            return {"temat": str(d.get("temat", "")).strip(),
                    "streszczenie": str(d.get("streszczenie", "")).strip()}
        except Exception:
            pass
    return {"temat": "", "streszczenie": tresc.strip()}


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
    """Zwraca {"temat": ..., "streszczenie": ...}.
    Rzuca RuntimeError po wyczerpaniu prób (np. limit 429 lub brak odpowiedzi)."""
    if not api_key:
        raise RuntimeError("Brak klucza API OpenRouter.")

    tresc_wejscia = (tekst or "")[:limit_znakow]
    user = (f"Sygnatura: {sygnatura}\nData wydania: {data_wyd}\n\n"
            f"TREŚĆ INTERPRETACJI:\n{tresc_wejscia}")

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": user},
        ],
        "temperature": 0.2,
        "max_tokens": 1200,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://skaner-doradca.streamlit.app",
        "X-Title": "Skaner Doradca",
    }

    ostatni_blad = None
    for i in range(proby):
        try:
            r = requests.post(ENDPOINT, headers=headers, json=payload, timeout=timeout)
        except requests.RequestException as e:
            ostatni_blad = f"Błąd sieci: {e}"
            time.sleep(3 * (i + 1))
            continue

        if r.status_code == 429:  # przekroczony limit zapytań — backoff
            ostatni_blad = "Przekroczony limit zapytań (429)."
            time.sleep(6 * (i + 1))
            continue
        if r.status_code >= 400:
            # 4xx/5xx — spróbuj odczytać komunikat i przerwij lub ponów
            try:
                info = r.json().get("error", {}).get("message", r.text[:200])
            except Exception:
                info = r.text[:200]
            ostatni_blad = f"HTTP {r.status_code}: {info}"
            if r.status_code in (401, 402, 403):  # klucz/kredyty — nie ponawiaj
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

        return _wyodrebnij_json(tresc)

    raise RuntimeError(ostatni_blad or "Nie udało się uzyskać streszczenia.")
