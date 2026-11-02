"""Country names, for telling a page about one country from a page about another. Common English names and a few aliases."""

from __future__ import annotations

import re

COUNTRIES: dict[str, tuple[str, ...]] = {
    "Afghanistan": (), "Albania": (), "Algeria": (), "Angola": (), "Argentina": (), "Armenia": (), "Australia": ("Australian",), "Austria": (),
    "Azerbaijan": (), "Bahrain": (), "Bangladesh": (), "Belarus": (), "Belgium": (), "Bolivia": (), "Bosnia": (), "Botswana": (), "Brazil": ("Brazilian",),
    "Brunei": (), "Bulgaria": (), "Cambodia": (), "Cameroon": (), "Canada": ("Canadian",), "Chile": (), "China": ("Chinese",), "Colombia": (),
    "Costa Rica": (), "Croatia": (), "Cuba": (), "Cyprus": (), "Czech Republic": ("Czechia",), "Denmark": ("Danish",), "Ecuador": (), "Egypt": ("Egyptian",),
    "Estonia": (), "Ethiopia": (), "Finland": ("Finnish",), "France": ("French",), "Georgia": (), "Germany": ("German",), "Ghana": (), "Greece": ("Greek",),
    "Hong Kong": (), "Hungary": (), "Iceland": (), "India": ("Indian",), "Indonesia": ("Indonesian",), "Iran": ("Iranian",), "Iraq": (), "Ireland": ("Irish",),
    "Israel": ("Israeli",), "Italy": ("Italian",), "Jamaica": (), "Japan": ("Japanese",), "Jordan": (), "Kazakhstan": (), "Kenya": (), "Kuwait": (),
    "Laos": (), "Latvia": (), "Lebanon": (), "Libya": (), "Lithuania": (), "Luxembourg": (), "Macau": ("Macao",), "Malaysia": ("Malaysian",), "Malta": ("Maltese",),
    "Mexico": ("Mexican",), "Mongolia": (), "Morocco": (), "Mozambique": (), "Myanmar": (), "Nepal": (), "Netherlands": ("Dutch",), "New Zealand": (),
    "Nigeria": (), "North Korea": (), "Norway": ("Norwegian",), "Oman": (), "Pakistan": ("Pakistani",), "Panama": (), "Peru": (), "Philippines": ("Filipino",),
    "Poland": ("Polish",), "Portugal": ("Portuguese",), "Qatar": ("Qatari",), "Romania": (), "Russia": ("Russian",), "Rwanda": (), "Saudi Arabia": ("Saudi",),
    "Serbia": (), "Singapore": (), "Slovakia": (), "Slovenia": (), "South Africa": (), "South Korea": ("Korea", "Korean"), "Spain": ("Spanish",),
    "Sri Lanka": (), "Sweden": ("Swedish",), "Switzerland": ("Swiss",), "Syria": (), "Taiwan": ("Taiwanese",), "Tanzania": (), "Thailand": ("Thai",),
    "Tunisia": (), "Turkey": ("Turkish", "Türkiye"), "Uganda": (), "Ukraine": ("Ukrainian",), "United Arab Emirates": ("UAE", "Emirati", "Abu Dhabi", "Dubai"),
    "United Kingdom": ("UK", "Britain", "British", "England"), "United States": ("U.S.", "USA", "America", "American"), "Uruguay": (), "Uzbekistan": (),
    "Venezuela": (), "Vietnam": ("Vietnamese", "Viet Nam"), "Yemen": (), "Zambia": (), "Zimbabwe": (),
}
_SAME = {"united states of america": "United States", "usa": "United States", "us": "United States", "republic of korea": "South Korea", "korea": "South Korea",
         "uae": "United Arab Emirates", "abu dhabi": "United Arab Emirates", "dubai": "United Arab Emirates", "uk": "United Kingdom", "great britain": "United Kingdom",
         "people's republic of china": "China", "prc": "China", "chinese taipei": "Taiwan", "viet nam": "Vietnam", "czechia": "Czech Republic", "türkiye": "Turkey"}
_PATTERNS = {name: re.compile(r"(?<![\w])(?:" + "|".join(re.escape(alias) for alias in (name, *aliases)) + r")(?![\w])", re.I)
             for name, aliases in COUNTRIES.items()}


def canonical(country: str) -> str | None:
    text = " ".join(str(country or "").split())
    if not text:
        return None
    return _SAME.get(text.casefold()) or next((name for name in COUNTRIES if name.casefold() == text.casefold()), text)


def named(text: str) -> set[str]:
    """The countries a text names, by common name or alias. 'US' is matched in capitals only, so 'us' the word is not a country."""
    found = {name for name, pattern in _PATTERNS.items() if pattern.search(text)}
    if re.search(r"(?<![\w])US(?![\w])", text):
        found.add("United States")
    return found
