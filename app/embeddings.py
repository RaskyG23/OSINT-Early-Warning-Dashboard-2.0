"""Small, reproducible corpus-trained word embeddings for semantic risk cues.

The model uses Positive Pointwise Mutual Information (PPMI) over a sliding
context window.  It is intentionally dependency-free and trains on the news
already stored by the dashboard, so Docker does not need a large downloaded
language model.
"""

from collections import Counter, defaultdict
from dataclasses import dataclass
from math import log, sqrt
import re


TOKEN_RE = re.compile(r"[a-z]+(?:-[a-z]+)?")
SEED_GROUPS = {
    "disruption": {"closure", "delay", "blocked", "shortage", "shutdown", "damage", "rerouting", "outage"},
    "severity": {"war", "attack", "explosion", "earthquake", "cyclone", "fatalities", "emergency"},
    "transport": {"shipping", "port", "cargo", "freight", "airport", "airspace", "rail", "logistics"},
}


def _tokens(text):
    return TOKEN_RE.findall((text or "").casefold())


def _cosine(left, right):
    if not left or not right:
        return 0.0
    shared = left.keys() & right.keys()
    numerator = sum(left[key] * right[key] for key in shared)
    left_norm = sqrt(sum(value * value for value in left.values()))
    right_norm = sqrt(sum(value * value for value in right.values()))
    return numerator / (left_norm * right_norm) if left_norm and right_norm else 0.0


def _average(vectors):
    output = defaultdict(float)
    vectors = list(vectors)
    if not vectors:
        return {}
    for vector in vectors:
        for key, value in vector.items():
            output[key] += value / len(vectors)
    return dict(output)


@dataclass
class PPMIEmbedding:
    vectors: dict
    prototypes: dict
    document_count: int
    vocabulary_size: int
    trained: bool

    def vector(self, text):
        """Return the averaged corpus-trained vector for arbitrary text."""
        words = _tokens(text)
        return _average(self.vectors[word] for word in words if word in self.vectors)

    def scores(self, text):
        words = _tokens(text)
        article = self.vector(text)
        coverage = len({word for word in words if word in self.vectors}) / max(1, len(set(words)))
        return {
            name: round(_cosine(article, prototype), 4)
            for name, prototype in self.prototypes.items()
        } | {"coverage": round(coverage, 4), "trained": self.trained}


def train_ppmi_embeddings(documents, window=4, min_frequency=2, minimum_documents=20):
    tokenised = [_tokens(document) for document in documents if document]
    frequencies = Counter(word for document in tokenised for word in document)
    vocabulary = {word for word, count in frequencies.items() if count >= min_frequency}
    cooccurrence = Counter()
    row_totals = Counter()
    context_totals = Counter()
    for document in tokenised:
        words = [word for word in document if word in vocabulary]
        for index, word in enumerate(words):
            for context in words[max(0, index-window):index] + words[index+1:index+window+1]:
                cooccurrence[(word, context)] += 1
                row_totals[word] += 1
                context_totals[context] += 1
    total = sum(cooccurrence.values())
    vectors = defaultdict(dict)
    if total:
        for (word, context), count in cooccurrence.items():
            pmi = log((count * total) / (row_totals[word] * context_totals[context]))
            if pmi > 0:
                vectors[word][context] = pmi
    vectors = dict(vectors)
    prototypes = {
        name: _average(vectors[word] for word in seeds if word in vectors)
        for name, seeds in SEED_GROUPS.items()
    }
    trained = len(tokenised) >= minimum_documents and bool(vectors) and all(prototypes.values())
    return PPMIEmbedding(vectors, prototypes, len(tokenised), len(vectors), trained)
