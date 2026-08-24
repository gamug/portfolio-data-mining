"""Splits long article bodies into <=512-token windows for BERT-family models.

Articles in this corpus range up to ~13K words, far past BERT's 512-token limit,
so both the sentiment and NER stages need chunking. Chunks are built by packing
whole sentences (never splitting a sentence mid-way) so entity spans are never
cut in half by a chunk boundary in the common case.
"""

import re
from dataclasses import dataclass
from typing import Any

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'])")


@dataclass
class Chunk:
    text: str
    start_char: int  # offset of this chunk's text within the original article
    end_char: int


def split_sentences(text: str) -> list[tuple[str, int, int]]:
    """Return (sentence_text, start_char, end_char) for each sentence, offsets
    relative to `text`. Regex-based; approximate but adequate for news prose."""
    sentences = []
    pos = 0
    for part in _SENTENCE_SPLIT_RE.split(text):
        if not part:
            continue
        start = text.index(part, pos)
        end = start + len(part)
        sentences.append((part, start, end))
        pos = end
    return sentences


def chunk_text(text: str, tokenizer: Any, max_tokens: int = 510) -> list[Chunk]:
    """Pack sentences into chunks whose tokenized length stays under max_tokens
    (leaving room for [CLS]/[SEP]). A single sentence longer than max_tokens is
    hard-split at the token level as a fallback.
    """
    sentences = split_sentences(text)
    if not sentences:
        return []

    chunks: list[Chunk] = []
    cur_sentences: list[tuple[str, int, int]] = []
    cur_len = 0

    def flush() -> None:
        nonlocal cur_sentences, cur_len
        if cur_sentences:
            start = cur_sentences[0][1]
            end = cur_sentences[-1][2]
            chunks.append(Chunk(text=text[start:end], start_char=start, end_char=end))
        cur_sentences = []
        cur_len = 0

    for sent, s_start, s_end in sentences:
        n_tok = len(tokenizer.encode(sent, add_special_tokens=False))

        if n_tok > max_tokens:
            # Sentence itself exceeds the budget: flush what we have, then
            # hard-split this sentence at the token level.
            flush()
            enc = tokenizer(sent, add_special_tokens=False, return_offsets_mapping=True)
            offsets = enc["offset_mapping"]
            for i in range(0, len(offsets), max_tokens):
                window = offsets[i : i + max_tokens]
                if not window:
                    continue
                w_start = s_start + window[0][0]
                w_end = s_start + window[-1][1]
                chunks.append(Chunk(text=text[w_start:w_end], start_char=w_start, end_char=w_end))
            continue

        if cur_len + n_tok > max_tokens:
            flush()

        cur_sentences.append((sent, s_start, s_end))
        cur_len += n_tok

    flush()
    return chunks


def merge_char_spans(article_entities: list[dict]) -> list[dict]:
    """Merge entity spans from multiple chunks, dropping exact duplicates that
    can occur when adjacent chunks both touch the same boundary token."""
    seen = set()
    merged = []
    for e in article_entities:
        key = (e["entity_type"], e["start_char"], e["end_char"])
        if key in seen:
            continue
        seen.add(key)
        merged.append(e)
    return merged
