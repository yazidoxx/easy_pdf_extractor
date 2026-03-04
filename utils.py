"""Utility functions for cleaning and post‑processing PDF text.

The functions in this module are intentionally focused on pure string
transformations so they are easy to test and reason about. Behaviour is
preserved from the original implementation.
"""

from __future__ import annotations

import re
from typing import Iterable, Mapping


def clean_string(text: str) -> str:
    """Apply a series of domain-specific text normalisations.

    This function performs targeted replacements that fix common OCR
    artefacts and typographical errors observed in the input PDFs.

    Args:
        text: Raw text to clean.

    Returns:
        Cleaned text with specific substrings normalised.
    """
    text = text.replace("Conicts of Interest", "Conflict of Interest")
    text = text.replace("condential", "confidential")
    text = text.replace("supplement le", "supplement file")
    text = text.replace("Charit ", "Charité ")
    text = text.replace("Charite ", "Charité ")
    text = text.replace("Charite ", "Charité ")
    text = text.replace("nngen./", "finngen.fi/")
    text = text.replace("Source Data le", "Source Data file")
    text = text.replace("the ndings", "the findings")
    text = text.replace("Data and code availability d", "Data and code availability")
    text = text.replace(".gshare", ".figshare")
    text = text.replace("/gshare", "/figshare")
    text = text.replace("num ber", "number")
    text = text.replace("Pzer", "Pfizer")
    text = text.replace("(ttps", "(https")
    text = text.replace("data les", "data files")
    text = text.replace("data_les", "data_files")
    text = text.replace("additional le", "additional file")
    text = text.replace("data le", "data file")
    text = text.replace("da ta", "data")
    text = text.replace("/ m9", "/m9")
    text = text.replace("Conict of Interest", "Conflict of Interest")
    text = text.replace("pub licly", "publicly")
    text = text.replace("comprise", "compromise")
    text = text.replace("sensi ble", "sensible")
    text = text.replace(
        "xperimental model and subject det",
        "Experimental model and subject details",
    )
    text = re.sub(r"Materials and methods\s*Study", "Materials and methods \nStudy", text)
    # If "Results" is at the beginning of a line and directly followed by a word starting with uppercase, insert a newline between them
    
    text = text.replace(
        "XPERIMENTAL MODEL AND SUBJECT DETA",
        "Experimental model and subject details",
    )
    text = text.replace("Methods2", "Methods")
    # Normalise broken "www." occurrences where whitespace is inserted.
    text = re.sub(r"www\.\s+", "www.", text)
    return text


def format_url_string_pattern(input_string: str) -> str:
    """Create a regex pattern that matches a URL with flexible whitespace.

    The resulting pattern tolerates arbitrary spaces and newlines between
    characters and correctly escapes special characters so it can be used
    with :func:`re.sub`.

    Args:
        input_string: The URL string to format.

    Returns:
        A regex pattern for flexible matching of the given URL.
    """
    join_non_special_char = r"(\s*|\\n*)"
    join_special_char = r"(\s*|\\n*)" + "\\"

    result: list[str] = []

    def is_special_char(char: str) -> bool:
        special_chars = "!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~"
        return char in special_chars

    for char in input_string:
        if is_special_char(char):
            result.append(join_special_char + char)
        else:
            result.append(join_non_special_char + char)

    return "".join(result)


def extract_links(link_data: Iterable[Mapping[str, object]]) -> list[str]:
    """Extract unique URLs from a list of link dictionaries.

    Mirrors the original behaviour (including loss of ordering).

    Args:
        link_data: Iterable of dictionaries containing link metadata with an
            optional ``"uri"`` key.

    Returns:
        List of unique URL strings.
    """
    links = [link["uri"] for link in link_data if "uri" in link]
    # ``set`` preserves uniqueness but not order; original implementation did
    # the same, so we keep that behaviour.
    return list(set(links))  # type: ignore[index]


def join_text(text: str) -> str:
    """Join lines and normalise spacing in a text block.

    Args:
        text: The text to join.

    Returns:
        Text with line breaks removed, some special characters normalised,
        and repeated spaces collapsed.
    """
    text = text.replace("¼", "=")
    text = " ".join(text.split("\n"))
    text = text.replace("- ", "").replace("  ", " ")
    return text


def replace_text_with_links(text: str, links: Iterable[str]) -> str:
    """Replace fuzzy URL patterns in ``text`` with concrete link values.

    Args:
        text: The text to search and replace links in.
        links: Iterable of link strings to replace in the text.

    Returns:
        Text with recognised link patterns replaced by the corresponding URLs.
    """
    for link in links:
        if "penalty" in link or "ignorespaces" in link:
            continue
        try:
            normalised_link = link.replace("%20\\l%20", "#")
            pattern = format_url_string_pattern(str(normalised_link))
            text = re.sub(pattern, f" {normalised_link} ", text, flags=re.IGNORECASE)
        except re.error:
            # Preserve original side effect of printing problematic links.
            print(f"Error with link: {link}")
    return text


def process_page_text(text: str, links: Iterable[str] | None) -> str:
    """Clean and post‑process text extracted from a single PDF page.

    Behaviour matches the original implementation: text is cleaned,
    links are replaced when provided, and then lines are joined with
    an extra newline appended before joining.

    Args:
        text: Raw text extracted from a page region.
        links: Iterable of links present on the page, or ``None``.

    Returns:
        Normalised page text.
    """
    text = clean_string(text)

    if links:
        text = replace_text_with_links(text, links)

    text = join_text(text + "\n")
    return text


def remove_unicode(text: str) -> str:
    """Strip non‑ASCII characters from ``text`` while preserving ASCII hyphens.

    Args:
        text: Text to normalise.

    Returns:
        Text containing only ASCII characters. Non‑ASCII hyphen-like characters
        are first converted to the ASCII hyphen-minus (``-``).
    """
    # Convert common non‑ASCII hyphen characters to the ASCII hyphen.
    text = text.replace("–", "-").replace("—", "-").replace("‐", "-")
    # Replace non-breaking space with regular space.
    text = text.replace("\xa0", "")
    # Remove all non-ASCII characters.
    text = re.sub(r"[^\x00-\x7F]", "", text)
    return text.encode("ascii", "ignore").decode("ascii")