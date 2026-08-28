import unittest
from datetime import datetime, timedelta, timezone

from app.forecasting import forecast_country


class ForecastingTests(unittest.TestCase):
    def article(self, days_ago, score, sources=2):
        return {
            "headline": f"Operational event score {score}",
            "published_at": (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat(),
            "risk": {
                "score": score, "level": "Critical" if score >= 70 else "Moderate" if score >= 38 else "Low",
                "confidence": 65, "source_count": sources, "primary_source_count": 0,
                "effects": ["possible port, ocean-freight, routing or lead-time disruption"],
            },
        }

    def test_increasing_impacts_produce_escalating_projection(self):
        articles = [self.article(4, 35), self.article(2, 48), self.article(0, 62)]
        result = forecast_country("Exampleland", articles, {"score": 55})
        self.assertTrue(result["available"])
        self.assertEqual(result["direction"], "Escalating")
        self.assertGreater(result["projections"][1]["score"], result["projections"][0]["score"])
        self.assertEqual(result["scenarios"][0]["supporting_events"], 3)

    def test_no_fresh_events_returns_insufficient_evidence(self):
        result = forecast_country("Exampleland", [], {"score": 0})
        self.assertFalse(result["available"])
        self.assertIn("No fresh", result["reason"])

    def test_projection_is_bounded_to_risk_scale(self):
        articles = [self.article(2, 85), self.article(1, 95), self.article(0, 100)]
        result = forecast_country("Exampleland", articles, {"score": 98})
        self.assertTrue(all(0 <= item["score"] <= 100 for item in result["projections"]))
        self.assertTrue(all(0 <= item["low"] <= item["high"] <= 100 for item in result["projections"]))


if __name__ == "__main__":
    unittest.main()
