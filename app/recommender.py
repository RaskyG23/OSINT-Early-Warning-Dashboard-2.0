"""Explainable hybrid news recommender for supply-chain intelligence.

The implementation remains dependency-light for the Docker deployment. It
combines corpus-trained PPMI word embeddings, a small online logistic model,
operational guardrails, recency decay and maximal marginal relevance (MMR).
Explicit feedback changes ranking only; it never changes risk or confidence.
"""

import hashlib
import math
import re
from collections import Counter
from datetime import datetime, timezone

from app.embeddings import train_ppmi_embeddings


STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "has", "in", "is",
    "it", "its", "of", "on", "or", "that", "the", "this", "to", "was", "were", "with",
    "latest", "news", "reported", "reports", "update",
}


def article_key(article):
    identity = article.get("url") or f'{article.get("country", "")}|{article.get("headline", "")}'
    return hashlib.sha1(identity.encode("utf-8")).hexdigest()


def article_text(article):
    return " ".join(str(article.get(field) or "") for field in (
        "headline", "summary", "category", "country", "transport_mode"
    ))


def tokens(text):
    return [word for word in re.findall(r"[a-z][a-z0-9-]{2,}", text.casefold()) if word not in STOPWORDS]


def _vector(document, idf):
    counts = Counter(tokens(document))
    total = sum(counts.values()) or 1
    return {term: (count / total) * idf.get(term, 1.0) for term, count in counts.items()}


def _cosine(left, right):
    numerator = sum(value * right.get(term, 0) for term, value in left.items())
    left_norm = math.sqrt(sum(value * value for value in left.values()))
    right_norm = math.sqrt(sum(value * value for value in right.values()))
    return numerator / (left_norm * right_norm) if left_norm and right_norm else 0.0


def _feedback_weight(row, now=None, half_life_days=90):
    """Exponentially reduce the influence of old choices without deleting them."""
    now = now or datetime.now(timezone.utc)
    try:
        updated = datetime.fromisoformat(str(row.get("updated_at", "")).replace("Z", "+00:00"))
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        age_days = max(0.0, (now - updated.astimezone(timezone.utc)).total_seconds() / 86400)
        return math.exp(-math.log(2) * age_days / half_life_days)
    except (TypeError, ValueError):
        return 1.0


def _hashed_features(document, dimensions=192):
    """Stable sparse features for the incremental preference classifier."""
    output = {}
    terms = tokens(document)
    features = terms + [f"{left}_{right}" for left, right in zip(terms, terms[1:])]
    total = len(features) or 1
    for term in features:
        digest = hashlib.sha1(term.encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "big") % dimensions
        sign = 1 if digest[4] % 2 == 0 else -1
        output[index] = output.get(index, 0.0) + sign / total
    return output


def _dot(weights, vector):
    return sum(weights[index] * value for index, value in vector.items())


def _sigmoid(value):
    value = max(-30.0, min(30.0, value))
    return 1.0 / (1.0 + math.exp(-value))


def _train_preference_model(feedback_rows, epochs=18, learning_rate=0.35, dimensions=192):
    """Fit a compact, deterministic logistic model from explicit profile feedback."""
    labels = {int(row.get("feedback") or 0) for row in feedback_rows}
    if len(feedback_rows) < 4 or not {-1, 1}.issubset(labels):
        return None
    weights = [0.0] * dimensions
    bias = 0.0
    examples = [(_hashed_features(article_text(row), dimensions), 1.0 if row["feedback"] == 1 else 0.0,
                 _feedback_weight(row)) for row in feedback_rows]
    for epoch in range(epochs):
        rate = learning_rate / (1.0 + epoch * 0.12)
        for vector, target, importance in examples:
            prediction = _sigmoid(bias + _dot(weights, vector))
            error = (target - prediction) * importance
            bias += rate * error
            for index, value in vector.items():
                weights[index] = weights[index] * (1.0 - rate * 0.001) + rate * error * value
    return weights, bias


def _recency(article, now=None):
    now = now or datetime.now(timezone.utc)
    try:
        published = datetime.fromisoformat(str(article.get("published_at", "")).replace("Z", "+00:00"))
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        age_hours = max(0, (now - published.astimezone(timezone.utc)).total_seconds() / 3600)
        return math.exp(-age_hours / 72)
    except (TypeError, ValueError):
        return 0.25


def rank_articles(articles, feedback_rows, diversity=0.78):
    """Rank articles with semantic learning, safety guardrails and MMR diversity."""
    if not articles:
        return []
    documents = [article_text(item) for item in articles] + [article_text(item) for item in feedback_rows]
    document_tokens = [set(tokens(document)) for document in documents]
    count = len(documents)
    vocabulary = set().union(*document_tokens) if document_tokens else set()
    idf = {term: math.log((1 + count) / (1 + sum(term in doc for doc in document_tokens))) + 1
           for term in vocabulary}
    lexical_vectors = [_vector(document, idf) for document in documents]
    semantic_model = train_ppmi_embeddings(documents, min_frequency=1, minimum_documents=4)
    semantic_vectors = [semantic_model.vector(document) for document in documents]
    article_count = len(articles)
    positive = [(semantic_vectors[article_count + index] or lexical_vectors[article_count + index],
                 _feedback_weight(item)) for index, item in enumerate(feedback_rows) if item.get("feedback") == 1]
    negative = [(semantic_vectors[article_count + index] or lexical_vectors[article_count + index],
                 _feedback_weight(item)) for index, item in enumerate(feedback_rows) if item.get("feedback") == -1]

    def affinity(vector):
        liked_total = sum(weight for _, weight in positive) or 1
        disliked_total = sum(weight for _, weight in negative) or 1
        liked = sum(_cosine(vector, item) * weight for item, weight in positive) / liked_total if positive else 0
        disliked = sum(_cosine(vector, item) * weight for item, weight in negative) / disliked_total if negative else 0
        return max(0.0, min(1.0, 0.5 + liked * 0.5 - disliked * 0.5))

    ranked = []
    trained = bool(positive or negative)
    classifier = _train_preference_model(feedback_rows)
    for index, article in enumerate(articles):
        comparison_vector = semantic_vectors[index] or lexical_vectors[index]
        semantic = affinity(comparison_vector) if trained else 0.5
        learned = _sigmoid(classifier[1] + _dot(classifier[0], _hashed_features(article_text(article)))) if classifier else semantic
        preference = 0.6 * learned + 0.4 * semantic if classifier else semantic
        risk = article.get("risk") or {}
        relevance = max(0, min(100, int(article.get("country_relevance_score") or 0))) / 100
        risk_score = max(0, min(100, int(risk.get("score") or 0))) / 100
        confidence = max(0, min(100, int(risk.get("confidence") or 0))) / 100
        recency = _recency(article)
        score = (0.35 * preference + 0.25 * semantic + 0.15 * relevance
                 + 0.10 * recency + 0.10 * risk_score + 0.05 * confidence)
        enriched = article.copy()
        enriched["recommendation_score"] = round(score * 100)
        enriched["interest_similarity"] = round(semantic * 100)
        enriched["learned_interest_score"] = round(learned * 100)
        enriched["recommendation_trained"] = trained
        enriched["recommendation_model"] = "Hybrid semantic + online logistic" if classifier else "Semantic preference" if trained else "Operational cold start"
        enriched["_recommendation_vector"] = comparison_vector
        # A strongly supported critical warning is pinned ahead of personalisation.
        enriched["_mandatory_alert"] = bool(
            risk.get("level") == "Critical" and (confidence >= 0.60 or risk_score >= 0.75)
        )
        ranked.append(enriched)

    # MMR keeps the final list useful by penalising near-duplicates. Mandatory
    # alerts remain in the first tier and can never be hidden by preference.
    selected, remaining = [], ranked[:]
    while remaining:
        best = max(remaining, key=lambda item: (
            1 if item["_mandatory_alert"] else 0,
            diversity * item["recommendation_score"] / 100
            - (1 - diversity) * max((_cosine(item["_recommendation_vector"], chosen["_recommendation_vector"])
                                     for chosen in selected), default=0),
            item.get("published_at") or "",
        ))
        remaining.remove(best)
        selected.append(best)
    for item in selected:
        item.pop("_recommendation_vector", None)
        item.pop("_mandatory_alert", None)
    return selected


def recommendation_reason(article, feedback_rows):
    if not feedback_rows:
        return "Chronological default until you rate some stories."
    candidate = set(tokens(article_text(article)))
    liked_terms = Counter()
    disliked_terms = Counter()
    for item in feedback_rows:
        target = liked_terms if item.get("feedback") == 1 else disliked_terms
        target.update(set(tokens(article_text(item))))
    matches = [term for term, _ in liked_terms.most_common() if term in candidate and not disliked_terms[term]][:3]
    if matches:
        return "Matches your interest in " + ", ".join(matches) + "; balanced with operational importance and diversity."
    return "Ranked using semantic similarity and your previous choices, while preserving urgent warnings and a diverse set of developments."
