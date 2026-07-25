/**
 * sygnatury.js — normalizacja sygnatur po stronie wtyczki.
 *
 * BLIŹNIACZA IMPLEMENTACJA normalizacja_sygnatur.py. Obie muszą dawać
 * IDENTYCZNY wynik dla każdego wejścia — inaczej wtyczka będzie pytać bazę
 * o klucz, którego baza nie zna, i dostawać ciche pudła.
 *
 * Parytetu pilnuje test_parytet.py: przepuszcza ten sam korpus przez Pythona
 * i przez Node, po czym porównuje wyniki znak po znaku.
 * ZMIENIASZ TUTAJ — ZMIEŃ TAM I URUCHOM TEST PARYTETU.
 *
 * Plik jest ładowany jako zwykły skrypt (bez modułów ES), tak samo jak
 * prompty.js, i wystawia globalny obiekt SYGNATURY.
 */

'use strict';

const SYGNATURY = (() => {

  // Znaki niewidoczne: miękki dywiz, spacje zerowej szerokości, BOM.
  const NIEWIDOCZNE = /[\u00ad\u200b\u200c\u200d\u2060\ufeff]/g;

  // Warianty myślnika sprowadzane do "-".
  const MYSLNIKI = /[\u2010\u2011\u2012\u2013\u2014\u2015\u2212\ufe58\ufe63\uff0d]/g;

  // Interpunkcja obcinana z brzegów. Klasa musi odpowiadać stałej OBCINANE
  // w wersji pythonowej.
  const OBCINANE = '[.,;:()\\[\\]{}"\'«»„”“’ \\t]';
  const OBCINANE_POCZATEK = new RegExp('^' + OBCINANE + '+');
  const OBCINANE_KONIEC   = new RegExp(OBCINANE + '+$');

  /**
   * Sprowadza sygnaturę do postaci kanonicznej.
   * Kolejność kroków musi być identyczna jak w Pythonie.
   */
  function normalizuj(surowa) {
    if (!surowa) return '';

    let t = String(surowa).normalize('NFKC');

    t = t.replace(NIEWIDOCZNE, '');
    t = t.replace(MYSLNIKI, '-');
    t = t.replace(/\s+/g, '');
    t = t.toUpperCase();
    t = t.replace(OBCINANE_POCZATEK, '').replace(OBCINANE_KONIEC, '');

    return t;
  }

  // Sygnatura KIS bez końcowych inicjałów pracownika. Klucz DRUGIEGO wyboru —
  // numer wersji celowo zostaje, bo „.1.” i „.2.” to różne pisma.
  //
  // Budowa — SZEŚĆ członów: 0114-KDIP2-2 . 4010 . 123 . 2026 . 1 . AS
  //                         prefiks       dział   nr    rok   wersja inicjały
  // Prefiks ma myślniki, ale nie ma kropek.
  const KIS_INICJALY = /^(\d{4}-[A-Z0-9\-]+\.\d{3,4}\.\d+\.\d{4}\.\d+)\.[A-Z]{2,4}$/;

  function bezInicjalow(znormalizowana) {
    if (!znormalizowana) return '';
    const m = KIS_INICJALY.exec(znormalizowana);
    return m ? m[1] : znormalizowana;
  }

  // ---- rozpoznanie rodzaju (tylko diagnostyka) ----
  const WZ_KIS       = /^\d{4}-[A-Z]{2,6}[0-9.\-]*\.\d{3,4}\.\d+\.\d{4}\.\d+\.[A-Z]{2,4}$/;
  const WZ_KIS_LUZNO = /^\d{4}-[A-Z]/;
  const WZ_SAD       = /^[IVX]{1,4}[A-ZŁŚŻĆŃÓĄĘ]{1,6}(\/[A-ZŁŚŻĆŃÓĄĘ]{1,3})?\d+\/\d{2,4}$/;
  const WZ_IZBA      = /^[A-Z]{2,6}\d?\/\d{3,4}-\d+\/\d{2}(-\d+)?\/[A-Z]{2,3}$/;

  function rodzaj(znormalizowana) {
    if (!znormalizowana) return 'NIEZNANY';
    if (WZ_KIS.test(znormalizowana)) return 'KIS';
    if (WZ_SAD.test(znormalizowana)) return 'SAD';
    if (WZ_IZBA.test(znormalizowana)) return 'IZBA';
    if (WZ_KIS_LUZNO.test(znormalizowana)) return 'KIS?';
    return 'NIEZNANY';
  }

  return { normalizuj, bezInicjalow, rodzaj };
})();

// Eksport dla Node (test parytetu). W przeglądarce `module` nie istnieje
// i ta linia jest pomijana.
if (typeof module !== 'undefined' && module.exports) {
  module.exports = SYGNATURY;
}
