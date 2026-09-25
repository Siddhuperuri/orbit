"""Token counting without a tokenizer download.

`tiktoken` would be exact for OpenAI models, but it fetches its encoding files
from the network on first use -- incompatible with a worker and a test suite
that must run offline (ADR-0007). This approximation is calibrated to err
*high* for English prose (so chunks come out slightly under target rather than
over) and to stay safe for scripts where a character is roughly a token.

The error only moves chunk boundaries. Correctness does not depend on it: the
hard maximum of 768 approximate tokens is an order of magnitude inside
`text-embedding-3-small`'s 8,191-token input limit. ADR-0013 says tests assert
properties of chunks, never exact counts, for exactly this reason.
"""

from __future__ import annotations

import re

#: ASCII letter runs, digit runs, single non-space symbols, and runs of
#: newlines. Whitespace otherwise costs nothing: BPE tokenizers fold the
#: leading space into the following word's token.
_PIECES = re.compile(r"[A-Za-z]+|[0-9]+|\n+|[^\sA-Za-z0-9]")

#: Common words are single tokens; long words split roughly every 8 letters.
_LETTERS_PER_TOKEN = 8
#: Numbers are split into groups of up to three digits.
_DIGITS_PER_TOKEN = 3


class ApproximateTokenCounter:
    def count(self, text: str) -> int:
        total = 0
        for match in _PIECES.finditer(text):
            piece = match.group()
            first = piece[0]
            if first.isascii() and first.isalpha():
                total += 1 + (len(piece) - 1) // _LETTERS_PER_TOKEN
            elif first.isdigit() and first.isascii():
                total += -(-len(piece) // _DIGITS_PER_TOKEN)
            else:
                # Newline runs, punctuation, and every non-ASCII character
                # (accents, CJK, emoji) count one each.
                total += 1
        return total
