"""Identifier helpers (blueprint §4.2): symbol styles, CIK formatting, company-name keys.

Internally every symbol is Nasdaq style (`BRK.B`). EODHD and SEC write class shares with a
hyphen (`BRK-B`); EODHD marks superseded holders of a reused ticker `XXX_old`, `XXX_old1`, ...
"""

from __future__ import annotations

import re

import polars as pl

_OLD_SUFFIX = r"(?i)_old\d*$"
# Legal-form and share-class noise dropped when comparing company names.
_LEGAL_TOKENS = (
    "INC", "INCORPORATED", "CORP", "CORPORATION", "CO", "COMPANY", "COS", "LTD", "LIMITED", "PLC",
    "LLC", "LP", "HOLDINGS", "HOLDING", "GROUP", "THE", "NEW", "DEL", "NV", "SA", "AG", "CLASS",
    "CL", "COMMON", "STOCK", "SHS", "ORD",
)


def normalize_symbol(sym: str) -> str:
    """Any vendor style -> Nasdaq style: 'brk-b.US' -> 'BRK.B'."""
    s = sym.strip().upper()
    if s.endswith(".US"):
        s = s[:-3]
    return s.replace("-", ".").replace("/", ".")


def to_vendor(sym: str) -> str:
    """Nasdaq style -> the hyphenated style EODHD and SEC use: 'BRK.B' -> 'BRK-B'."""
    return normalize_symbol(sym).replace(".", "-")


def strip_old_suffix(code: str) -> str:
    return re.sub(_OLD_SUFFIX, "", code)


def cik10(cik: int | str) -> str:
    return f"{int(cik):010d}"


def symbol_expr(e: pl.Expr) -> pl.Expr:
    """Vectorized normalize_symbol, also dropping fja05680's '-YYYYMM' end-of-life suffix."""
    s = e.str.strip_chars().str.to_uppercase().str.replace(r"\.US$", "").str.replace(r"-\d{6}$", "")
    return s.str.replace_all("-", ".", literal=True).str.replace_all("/", ".", literal=True)


def name_key_expr(e: pl.Expr) -> pl.Expr:
    """Company name -> comparison key: upper case, no punctuation, state tags, legal forms.

    Apostrophes vanish (KOHL'S = KOHLS), runs of single letters join (V F = VF), and
    'Public Limited Company' is spelled PLC, so vendor and SEC spellings meet.
    """
    s = e.str.to_uppercase().str.replace_all("'", "", literal=True).str.replace_all("’", "", literal=True)
    s = s.str.replace_all("PUBLIC LIMITED COMPANY", "PLC", literal=True)
    s = s.str.replace_all("&", " AND ", literal=True)
    # SEC state tags look like 'APPLE INC /CA/' or 'XYZ CORP/DE'; delimit them so '/SWEETER' survives.
    s = s.str.replace_all(r"/[A-Z]{2,3}/", " ").str.replace_all(r"/[A-Z]{2,3}$", "")
    s = s.str.replace_all(r"\([^)]*\)", " ")
    s = s.str.replace_all(r"\bCL(ASS)?\.?\s+[A-Z]\b", " ")
    s = s.str.replace_all(r"[^A-Z0-9 ]+", " ")
    s = s.str.replace_all(r"\b(" + "|".join(_LEGAL_TOKENS) + r")\b", " ")
    s = s.str.replace_all(r"\s+", " ").str.strip_chars()
    # Join runs of single letters: "V F" -> "VF", "A O SMITH" -> "AO SMITH" (Rust regex has no
    # lookaround, so apply a pairwise join a few times; runs longer than four letters are rare).
    for _ in range(3):
        s = s.str.replace_all(r"(^|\s)([A-Z0-9]) ([A-Z0-9])(\s|$)", "$1$2$3$4")
    return s


def name_key(name: str) -> str:
    return str(pl.select(name_key_expr(pl.lit(name))).item())


def token_similarity(a: str, b: str) -> float:
    """Jaccard similarity of name-key tokens (0..1)."""
    ta, tb = set(name_key(a).split()), set(name_key(b).split())
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)
