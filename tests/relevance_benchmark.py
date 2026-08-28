"""Reproducible small hand-labelled benchmark for the relevance gate."""

from app.supply_chain import country_supply_chain_relevance


CASES = [
    ("Israel", "Wildcat labor strike brings Ben Gurion Airport to halt", "International", 1),
    ("Ghana", "GRIDCo engineers restore power after widespread outage", "Primary operational", 1),
    ("Moldova", "Checkpoint on border with Moldova operates after Russian attack", "Local / regional", 1),
    ("Iran", "Iranian ship in Caspian Sea hit by Ukrainian drone strike", "International", 1),
    ("India", "India power outage prompts residents to lock utility office", "Local / regional", 1),
    ("Nigeria", "Nigeria issues high-risk riverine flooding warning", "Primary operational", 1),
    ("China", "China port congestion causes delays and equipment shortages", "International", 1),
    ("Sudan", "Sudan Red Sea closure threatens critical aid supplies", "International", 1),
    ("Libya", "Libya power crisis and fuel diversion strain infrastructure", "International", 1),
    ("Iran", "United Arab Emirates suspends trade with Iran after missile fire", "International", 1),
    ("Russia", "Russian missile barrage strikes Kyiv in Ukraine", "International", 0),
    ("Poland", "Maine town challenges Poland Spring pumping after drought", "International", 0),
    ("Nigeria", "World Bank backs Nigeria's fight against drought", "International", 0),
    ("Israel", "Tsunami of emigration reshapes Israel's future", "International", 0),
    ("United Kingdom", "UK watchdog's war on drip pricing", "International", 0),
    ("Canada", "Canada condemns drone attack in Kurdistan", "International", 0),
    ("United States", "Somali piracy rises during U.S.-Iran war", "Primary operational", 0),
    ("Algeria", "Russian tanker aircraft rolls out; Algeria contract cited", "International", 0),
    ("Croatia", "War-crimes suspect arrested at Croatia-Montenegro border", "Local / regional", 0),
    ("Germany", "Germany opens drone research centre amid infrastructure threats", "International", 0),
]


def evaluate(threshold):
    tp = fp = tn = fn = 0
    for country, headline, scope, expected in CASES:
        predicted = country_supply_chain_relevance(
            {"headline": headline, "summary": "", "coverage_scope": scope},
            country, threshold,
        )["relevant"]
        tp += bool(expected and predicted)
        fp += bool(not expected and predicted)
        tn += bool(not expected and not predicted)
        fn += bool(expected and not predicted)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"threshold": threshold, "tp": tp, "fp": fp, "tn": tn, "fn": fn,
            "precision": precision, "recall": recall, "f1": f1}


if __name__ == "__main__":
    print("threshold tp fp tn fn precision recall f1")
    for value in (45, 50, 55, 60, 65, 70, 75, 80, 85, 90):
        row = evaluate(value)
        print("{threshold:>9} {tp:>2} {fp:>2} {tn:>2} {fn:>2} "
              "{precision:.3f} {recall:.3f} {f1:.3f}".format(**row))
