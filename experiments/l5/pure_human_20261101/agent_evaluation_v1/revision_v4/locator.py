"""Deterministic, auditable quotation location for PDF-derived text.

This module tolerates specified presentation differences only. It does not use
edit distance, stemming, paraphrase matching, lowercasing, or outside documents.
Every accepted match retains the exact original substring and character range.
A located quote is not a finding that the source semantically supports a claim.
"""
from __future__ import annotations

import re
import unicodedata

# Visual typography substitutions retain punctuation; they never delete a word,
# number, negation, or mathematical operator. Case remains significant.
_TYPOGRAPHY = str.maketrans({
    '\u2018': "'", '\u2019': "'", '\u201a': "'", '\u201b': "'",
    '\u201c': '"', '\u201d': '"', '\u201e': '"', '\u201f': '"',
    '\u2010': '-', '\u2011': '-', '\u2012': '-', '\u2013': '-',
    '\u2014': '-', '\u2015': '-', '\u2212': '-',
})
# A line-wrap hyphen is considered only between alphabetic characters. Keeping
# or removing that printed hyphen is an explicit, separately reported policy.
_WRAP = re.compile(r'(?<=[^\W\d_])-\r?\n[ \t]*(?=[^\W\d_])', re.UNICODE)


def _occurrences(text: str, quote: str) -> list[int]:
    starts, pos = [], 0
    while True:
        start = text.find(quote, pos)
        if start < 0:
            return starts
        starts.append(start)
        pos = start + 1


def _normal_form(text: str, wrap: str = 'none') -> tuple[str, list[tuple[int, int]]]:
    """Return a normalized string and original spans for each output character.

    Multiple normalized characters can map to one original compatibility glyph,
    e.g. the ligature ﬁ -> fi. The matcher checks cluster boundaries so it never
    accepts only half that original glyph as an otherwise exact quotation.
    """
    omit: set[int] = set()
    if wrap != 'none':
        for match in _WRAP.finditer(text):
            # 'keep' joins a wrapped compound retaining its literal hyphen;
            # 'remove' joins a word split with a printed line-wrap hyphen.
            start = match.start() + (1 if wrap == 'keep' else 0)
            omit.update(range(start, match.end()))
    characters: list[str] = []
    spans: list[tuple[int, int]] = []
    i = 0
    while i < len(text):
        if i in omit or text[i] == '\u00ad':  # Unicode discretionary soft hyphen
            i += 1
            continue
        end = i + 1
        # Normalize a base plus combining marks as one provenance cluster.
        while end < len(text) and end not in omit and unicodedata.combining(text[end]):
            end += 1
        normalized = unicodedata.normalize('NFKC', text[i:end]).translate(_TYPOGRAPHY)
        for char in normalized:
            if char == '\u00ad':
                continue
            if char.isspace():
                if characters and characters[-1] == ' ':
                    spans[-1] = (spans[-1][0], end)
                else:
                    characters.append(' ')
                    spans.append((i, end))
            else:
                characters.append(char)
                spans.append((i, end))
        i = end
    # Quotes normally do not include outer layout whitespace; trim symmetrically
    # without losing the original offsets of the retained text.
    first, last = 0, len(characters)
    while first < last and characters[first] == ' ':
        first += 1
    while last > first and characters[last - 1] == ' ':
        last -= 1
    return ''.join(characters[first:last]), spans[first:last]


def _mapped_occurrences(text: str, quote: str, wrap: str) -> list[tuple[int, int]]:
    normalized, mapping = _normal_form(text, wrap)
    needle, _ = _normal_form(quote, wrap)
    if not needle:
        return []
    found = []
    for start in _occurrences(normalized, needle):
        end = start + len(needle)
        # Do not locate a normalized substring inside an expanded source glyph.
        if start and mapping[start] == mapping[start - 1]:
            continue
        if end < len(mapping) and mapping[end - 1] == mapping[end]:
            continue
        original = (mapping[start][0], mapping[end - 1][1])
        if original not in found:
            found.append(original)
    return found


def locate(text: str, quote: str, occurrence: int = 0, context: str = 'quote',
           allow_unique_occurrence_correction: bool = True,
           allow_line_wrap_dehyphenation: bool = False) -> dict:
    """Locate a quote with a deterministic, original-character mapping.

    Policy order: exact -> NFKC/whitespace/typography/soft-hyphen -> joining a
    wrapped compound while retaining its hyphen. Removing a visible line-wrap
    hyphen is opt-in because it can make a genuine compound ambiguous.

    A wrong occurrence index is corrected only when the chosen policy has
    exactly one match; the correction is explicit in returned metadata. An
    ambiguous/missing quotation is rejected rather than guessed.
    """
    if not isinstance(text, str):
        raise ValueError(f'{context}: text must be a string')
    if not isinstance(quote, str) or not quote.strip():
        raise ValueError(f'{context}: quote must be a nonempty string')
    if type(occurrence) is not int or occurrence < 0:
        raise ValueError(f'{context}: occurrence must be a nonnegative integer')
    starts = _occurrences(text, quote)
    mode = 'exact'
    matches = [(start, start + len(quote)) for start in starts]
    if not matches:
        for mode, wrap in [('layout_nfkc_typography', 'none'),
                           ('layout_wrapped_hyphen_retained', 'keep')]:
            matches = _mapped_occurrences(text, quote, wrap)
            if matches:
                break
    if not matches and allow_line_wrap_dehyphenation:
        mode = 'layout_line_wrap_dehyphenated'
        matches = _mapped_occurrences(text, quote, 'remove')
    if not matches:
        raise ValueError(f'{context}: no deterministic literal/layout-equivalent quote match; '
                         'do not accept a paraphrase or infer missing evidence')
    selected = occurrence
    corrected = False
    if selected >= len(matches):
        if len(matches) == 1 and allow_unique_occurrence_correction:
            # A unique exact match is not enough to repair an index when
            # another presentation-equivalent location also exists.
            alternatives = set(matches)
            for wrap_policy in ['none', 'keep'] + (['remove'] if allow_line_wrap_dehyphenation else []):
                alternatives.update(_mapped_occurrences(text, quote, wrap_policy))
            if len(alternatives) != 1:
                raise ValueError(f'{context}: occurrence correction is ambiguous across '
                                 f'{len(alternatives)} literal/layout-equivalent locations')
            selected, corrected = 0, True
        else:
            raise ValueError(f'{context}: requested occurrence {occurrence}, but quotation has '
                             f'{len(matches)} matches; ambiguous occurrence is not corrected')
    start, end = matches[selected]
    return {
        'start': start, 'end': end, 'occurrence': selected,
        'occurrence_count': len(matches), 'match_mode': mode,
        'requested_quote': quote, 'exact_quote': text[start:end],
        'requested_occurrence': occurrence, 'occurrence_corrected': corrected,
        'line_wrap_dehyphenation_enabled': allow_line_wrap_dehyphenation,
    }
