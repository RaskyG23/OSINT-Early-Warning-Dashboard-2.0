"""Transparent multi-label taxonomy for general-news preference cards."""

import re


GENERAL_NEWS_TAXONOMY = {
    "Politics and governance": {
        "election", "government", "minister", "president", "parliament", "policy", "law",
        "regulation", "diplomatic", "diplomacy", "sanction", "cabinet", "vote", "court",
    },
    "Economy and finance": {
        "economy", "economic", "inflation", "interest", "rate", "bank", "banking", "finance",
        "financial", "market", "stocks", "currency", "investment", "recession", "gdp", "budget",
    },
    "Trade and supply chains": {
        "trade", "tariff", "customs", "import", "export", "supplier", "supply", "chain",
        "logistics", "freight", "cargo", "warehouse", "inventory", "shipment", "shipping",
    },
    "Transport and infrastructure": {
        "port", "airport", "rail", "railway", "road", "bridge", "transport", "transit",
        "aviation", "airline", "flight", "vessel", "ship", "terminal", "infrastructure",
    },
    "Energy and commodities": {
        "energy", "oil", "gas", "lng", "power", "electricity", "fuel", "petrol", "diesel",
        "commodity", "commodities", "mining", "copper", "grain", "wheat", "pipeline",
    },
    "Environment and climate": {
        "climate", "environment", "environmental", "emission", "pollution", "renewable",
        "carbon", "biodiversity", "conservation", "drought", "water", "weather",
    },
    "Natural disasters": {
        "earthquake", "tsunami", "flood", "flooding", "wildfire", "cyclone", "hurricane",
        "typhoon", "volcano", "eruption", "landslide", "storm", "disaster", "aftershock",
    },
    "Technology and cyber": {
        "technology", "digital", "software", "semiconductor", "chip", "cyber", "cyberattack",
        "ransomware", "artificial", "intelligence", "ai", "data", "telecom", "satellite",
    },
    "Conflict and security": {
        "war", "conflict", "attack", "missile", "drone", "military", "defence", "defense",
        "security", "terrorism", "ceasefire", "armed", "troops", "weapon", "hostilities",
    },
    "Health": {
        "health", "hospital", "disease", "virus", "outbreak", "pandemic", "medicine",
        "medical", "vaccine", "pharmaceutical", "patient", "public-health",
    },
    "Society and labour": {
        "strike", "worker", "workers", "labour", "labor", "employment", "unemployment",
        "wage", "protest", "education", "migration", "housing", "community", "inequality",
    },
}

GENERAL_NEWS_PHRASES = {
    "Politics and governance": ("prime minister", "foreign policy", "general election"),
    "Economy and finance": ("central bank", "interest rate", "stock market", "cost of living"),
    "Trade and supply chains": ("supply chain", "trade route", "customs clearance"),
    "Transport and infrastructure": ("air traffic", "public transport", "port authority"),
    "Energy and commodities": ("natural gas", "crude oil", "power grid"),
    "Environment and climate": ("climate change", "green energy"),
    "Natural disasters": ("natural disaster", "extreme weather"),
    "Technology and cyber": ("artificial intelligence", "data centre", "data center"),
    "Conflict and security": ("armed conflict", "national security"),
    "Health": ("public health", "health care", "healthcare system"),
    "Society and labour": ("industrial action", "labour union", "labor union"),
}


def _tokens(value):
    return set(re.findall(r"[a-z]+(?:-[a-z]+)?", (value or "").casefold()))


def classify_general_news(article, maximum_labels=3):
    """Assign up to three explainable categories using weighted text evidence."""
    headline = article.get("headline") or ""
    summary = article.get("summary") or article.get("description") or ""
    headline_tokens, summary_tokens = _tokens(headline), _tokens(summary)
    combined = f"{headline} {summary}".casefold()
    scores = {}
    evidence = {}
    for label, vocabulary in GENERAL_NEWS_TAXONOMY.items():
        headline_hits = sorted(headline_tokens & vocabulary)
        summary_hits = sorted(summary_tokens & vocabulary)
        phrase_hits = [phrase for phrase in GENERAL_NEWS_PHRASES.get(label, ()) if phrase in combined]
        score = 2 * len(headline_hits) + len(summary_hits) + 3 * len(phrase_hits)
        if score >= 2:
            scores[label] = score
            evidence[label] = (phrase_hits + headline_hits + summary_hits)[:5]
    ordered = sorted(scores, key=lambda label: (-scores[label], list(GENERAL_NEWS_TAXONOMY).index(label)))
    labels = ordered[:maximum_labels] or ["General affairs"]
    return {"labels": labels, "scores": {label: scores.get(label, 0) for label in labels},
            "evidence": {label: evidence.get(label, []) for label in labels},
            "method": "Weighted multi-label keyword and phrase taxonomy"}
