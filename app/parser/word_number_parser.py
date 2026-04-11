import re
from typing import Optional

ONES = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
    "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30,
    "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
    "eighty": 80, "ninety": 90,
    # Spanish
    "cero": 0, "uno": 1, "dos": 2, "tres": 3, "cuatro": 4,
    "cinco": 5, "seis": 6, "siete": 7, "ocho": 8, "nueve": 9,
    "diez": 10, "once": 11, "doce": 12, "trece": 13,
    "catorce": 14, "quince": 15, "dieciséis": 16, "dieciseis": 16,
    "diecisiete": 17, "dieciocho": 18, "diecinueve": 19,
    "veinte": 20, "treinta": 30, "cuarenta": 40, "cincuenta": 50,
    "sesenta": 60, "setenta": 70, "ochenta": 80, "noventa": 90,
    "cien": 100, "ciento": 100,
    "doscientos": 200, "trescientos": 300, "cuatrocientos": 400,
    "quinientos": 500, "seiscientos": 600, "setecientos": 700,
    "ochocientos": 800, "novecientos": 900,
}

MULTIPLIERS = {
    "hundred": 100, "thousand": 1000, "million": 1_000_000, "billion": 1_000_000_000,
    "mil": 1000, "millon": 1_000_000, "millón": 1_000_000,
    "millones": 1_000_000, "billon": 1_000_000_000,
}


def parse_word_numbers(text: str) -> Optional[float]:
    """
    Extracts the first number expressed in words from text.
    Handles mixed Spanish/English: "dos millones", "veinte mil", "two hundred fifty".
    Returns None if no word-number found.
    """
    text_lower = text.lower()
    # Normalize punctuation that might separate number words
    text_lower = re.sub(r"[,\.]", " ", text_lower)
    tokens = text_lower.split()

    result = 0
    current = 0
    found_any = False

    i = 0
    while i < len(tokens):
        word = tokens[i]

        if word in ONES:
            current += ONES[word]
            found_any = True
        elif re.match(r'^\d+$', word):
            current += int(word)
            found_any = True
        elif word in MULTIPLIERS:
            multiplier = MULTIPLIERS[word]
            if multiplier >= 1_000_000:
                result += (current if current > 0 else 1) * multiplier
                current = 0
            elif multiplier == 1000:
                result += (current if current > 0 else 1) * multiplier
                current = 0
            else:
                current = (current if current > 0 else 1) * multiplier
            found_any = True
        else:
            if found_any and (current > 0 or result > 0):
                break

        i += 1

    total = result + current
    return float(total) if found_any and total > 0 else None
