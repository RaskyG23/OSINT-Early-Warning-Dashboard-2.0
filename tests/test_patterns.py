import json
import tempfile
import unittest
from pathlib import Path

from app import database
from app.patterns import (
    analyze_observation,
    cusum_change_score,
    early_warning_assessment,
    extract_supply_chain_context,
    event_match,
    fact_variance,
    robust_z_score,
    same_story,
    story_key,
    synthesize_event_headline,
    synthesize_event_summary,
)
from app.supply_chain import assess_article, assess_country, country_supply_chain_relevance, tone_assessment


class PatternRecognitionTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        database.DB_PATH = Path(self.tempdir.name) / "patterns.sqlite"
        database.init_db()

    def tearDown(self):
        self.tempdir.cleanup()

    def test_story_similarity_clusters_reworded_headlines(self):
        left = story_key("Port workers strike closes Rotterdam container terminals")
        right = story_key("Rotterdam container terminals closed after port workers strike")
        self.assertTrue(same_story(left, right))

    def test_robust_z_uses_median_and_mad(self):
        self.assertEqual(robust_z_score(10, [2, 3, 3, 4, 4, 100]), 8.77)
        self.assertEqual(robust_z_score(10, [2, 3, 4, 5]), 0.0)

    def test_pattern_persists_across_refresh_windows(self):
        story = {
            "headline": "Port workers strike closes Rotterdam container terminals",
            "publisher": "Example News",
            "url": "https://example.test/one",
            "sources_json": json.dumps([{"publisher": "Example News", "url": "https://example.test/one"}]),
            "published_at": "2026-08-11T10:00:00+00:00",
        }
        analyze_observation("Netherlands", "Infrastructure and supply chains", [story], 4, "2026-08-11T10:05:00+00:00")
        story["headline"] = "Rotterdam container terminals closed after port workers strike"
        story["url"] = "https://example.test/two"
        story["sources_json"] = json.dumps([{"publisher": "Second Source", "url": story["url"]}])
        analyze_observation("Netherlands", "Infrastructure and supply chains", [story], 5, "2026-08-11T11:05:00+00:00")
        rows = database.country_patterns("Netherlands")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["active_windows"], 2)
        self.assertEqual(rows[0]["source_count"], 2)
        self.assertEqual(rows[0]["status"], "Developing")

    def test_phase_two_extracts_route_and_mode(self):
        context = extract_supply_chain_context(
            "Suez Canal closure diverts container shipping through the Red Sea"
        )
        self.assertIn("Suez Canal", context["routes"])
        self.assertIn("Red Sea", context["routes"])
        self.assertIn("Maritime", context["transport_modes"])
        self.assertGreaterEqual(context["operational_relevance"], 50)

    def test_phase_two_cusum_detects_upward_shift(self):
        score, status = cusum_change_score(10, [2, 3, 2, 3, 2, 3])
        self.assertGreaterEqual(score, 40)
        self.assertIn(status, {"Developing shift", "Structural shift"})

    def test_phase_three_warning_is_explainable(self):
        warning = early_warning_assessment(3.2, 72, 80, 4, 90, 8)
        self.assertEqual(warning["level"], "Critical")
        self.assertGreaterEqual(warning["confidence"], 60)
        self.assertTrue(any("source" in reason for reason in warning["rationale"]))

    def test_independent_primary_source_families_raise_confidence(self):
        article = {
            "headline": "Port closure disrupts container shipping",
            "summary": "A port closure is delaying freight.",
            "category": "Infrastructure and supply chains",
            "sources_json": json.dumps([
                {"publisher": "Port Authority", "source_family": "port.test", "source_type": "Port authority"},
                {"publisher": "Port Authority repost", "source_family": "port.test", "source_type": "Port authority"},
                {"publisher": "Independent News", "source_family": "news.test", "source_type": "News reporting"},
            ]),
        }
        result = assess_article(article)
        self.assertEqual(result["source_count"], 2)
        self.assertEqual(result["primary_source_count"], 1)
        self.assertGreaterEqual(result["confidence"], 60)

    def test_uncorroborated_sensor_has_limited_country_influence(self):
        articles = [{
            "headline": "Retail sales remain stable",
            "summary": "Routine market update.", "category": "Economy and markets",
            "published_at": "2026-08-12T10:00:00+00:00", "sources_json": "[]",
        }] * 5
        signals = [{"country": "Exampleland", "location": "Exampleland", "source": "GDELT",
                    "severity": "Critical", "event_type": "Armed conflict",
                    "title": "Armed conflict signal near Exampleland", "summary": "Conflict signal"}]
        result = assess_country("Exampleland", articles, signals)
        self.assertEqual(result["sensor_status"], "Provisional")
        self.assertEqual(result["sensor_weight"], 5)
        self.assertEqual(result["level"], "Low")

    def test_news_volume_alone_cannot_promote_all_low_stories(self):
        articles = [{
            "headline": f"Routine commercial update {index}", "summary": "Normal activity.",
            "category": "Economy and markets", "published_at": f"2026-08-12T1{index}:00:00+00:00",
            "sources_json": "[]",
        } for index in range(15)]
        result = assess_country("Exampleland", articles, [])
        self.assertEqual(result["level"], "Low")
        self.assertLess(result["score"], 38)

    def test_country_history_returns_distinct_latest_stories(self):
        story = {"headline": "Port closure delays cargo", "publisher": "Port Authority",
                 "url": "https://port.test/advisory", "sources_json": "[]",
                 "summary": "Cargo is delayed.", "published_at": "2026-08-10T10:00:00+00:00"}
        database.record_article_history("Exampleland", "Infrastructure and supply chains", [story],
                                        "2026-08-10T11:00:00+00:00", story_key)
        database.record_article_history("Exampleland", "Infrastructure and supply chains", [story],
                                        "2026-08-11T11:00:00+00:00", story_key)
        rows = database.country_article_history("Exampleland", 10)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["summary"], "Cargo is delayed.")

    def test_single_source_war_at_sea_is_inferred_moderate_exposure(self):
        article = {
            "headline": "Shipping attacks escalate as war talks hit an impasse",
            "summary": "A regional live update.", "category": "Infrastructure and supply chains",
            "sources_json": json.dumps([{"publisher": "One outlet", "source_family": "one.test",
                                           "source_type": "News reporting"}]),
        }
        result = assess_article(article)
        self.assertEqual(result["level"], "Moderate")
        self.assertFalse(result["provisional"])
        self.assertTrue(result["inferred_route_exposure"])

    def test_war_language_without_transport_exposure_remains_low(self):
        article = {
            "headline": "War talks reach another political impasse",
            "summary": "A regional diplomatic update.", "category": "Politics and governance",
            "sources_json": json.dumps([{"publisher": "One outlet", "source_family": "one.test",
                                           "source_type": "News reporting"}]),
        }
        result = assess_article(article)
        self.assertEqual(result["level"], "Low")
        self.assertTrue(result["provisional"])

    def test_concrete_transport_consequence_can_be_moderate(self):
        article = {
            "headline": "Port closure reroutes container shipping",
            "summary": "Cargo is delayed.", "category": "Infrastructure and supply chains",
            "sources_json": json.dumps([{"publisher": "One outlet", "source_family": "one.test",
                                           "source_type": "News reporting"}]),
        }
        result = assess_article(article)
        self.assertEqual(result["level"], "Moderate")
        self.assertFalse(result["provisional"])

    def test_corroborated_sensor_receives_full_weight(self):
        articles = [{
            "headline": "Missile attack disrupts airport freight operations",
            "summary": "Conflict forces an airport closure.", "category": "Conflicts and war",
            "published_at": "2026-08-12T10:00:00+00:00", "sources_json": "[]",
        }]
        signals = [{"country": "Exampleland", "location": "Exampleland", "source": "GDELT",
                    "severity": "Critical", "event_type": "Missile attack",
                    "title": "Missile attack signal near Exampleland", "summary": "Missile attack signal"}]
        result = assess_country("Exampleland", articles, signals)
        self.assertEqual(result["sensor_status"], "Corroborated")
        self.assertEqual(result["sensor_weight"], 25)

    def test_single_severe_corroborated_event_can_make_country_critical(self):
        severe = {
            "headline": "Missile attacks close port and airport, halting freight and shipping",
            "summary": "Infrastructure is damaged and cargo operations are suspended.",
            "category": "Infrastructure and supply chains", "published_at": "2026-08-12T10:00:00+00:00",
            "sources_json": json.dumps([
                {"publisher": "Port Authority", "source_family": "port.test", "source_type": "Port authority"},
                {"publisher": "Independent News", "source_family": "news.test", "source_type": "News reporting"},
            ]),
        }
        routine = {"headline": "Routine market update", "summary": "Normal activity.",
                   "category": "Economy and markets", "published_at": "2026-08-12T09:00:00+00:00",
                   "sources_json": "[]"}
        result = assess_country("Exampleland", [severe, routine, routine, routine, routine], [])
        self.assertEqual(result["level"], "Critical")
        self.assertGreaterEqual(result["score"], 75)
        self.assertIn("corroborated", result["escalation_reason"])

    def test_single_source_severe_headline_cannot_make_country_critical(self):
        severe = {
            "headline": "Missile attacks close port and halt shipping",
            "summary": "Cargo operations are suspended.", "category": "Infrastructure and supply chains",
            "published_at": "2026-08-12T10:00:00+00:00",
            "sources_json": json.dumps([{"publisher": "One outlet", "source_family": "one.test",
                                           "source_type": "News reporting"}]),
        }
        result = assess_country("Exampleland", [severe], [])
        self.assertNotEqual(result["level"], "Critical")

    def test_country_relevance_requires_country_in_publisher_headline(self):
        unrelated = {"headline": "Port closure delays cargo", "summary": "Grouped for Exampleland.",
                     "coverage_scope": "International"}
        relevant = {"headline": "Exampleland port closure delays cargo", "summary": "Cargo is delayed.",
                    "coverage_scope": "International"}
        self.assertFalse(country_supply_chain_relevance(unrelated, "Exampleland")["relevant"])
        self.assertTrue(country_supply_chain_relevance(relevant, "Exampleland")["relevant"])

    def test_tone_assessment_distinguishes_negative_and_positive_reporting(self):
        self.assertEqual(tone_assessment("Attack closes port and delays shipping")["label"], "Negative")
        self.assertEqual(tone_assessment("Port reopens and freight service resumes")["label"], "Positive")

    def test_event_anchor_matching_links_reworded_maritime_attack(self):
        long = "Shipping attacks escalate; 6 killed in Houthi attack in Bab el-Mandeb"
        short = "Pakistan says 3 citizens killed in Houthi attack on ship in Bab el-Mandeb"
        self.assertTrue(event_match(long, short))
        self.assertIn("Numeric details differ", fact_variance(long, short))

    def test_synthesized_headline_uses_shared_event_not_publisher_wording(self):
        headlines = [
            "Shipping attacks escalate; 6 killed in Houthi attack in Bab el-Mandeb - Outlet A",
            "Pakistan says 3 citizens killed in Houthi attack on ship in Bab el-Mandeb - Outlet B",
        ]
        result = synthesize_event_headline(headlines, "Israel")
        self.assertIn("Houthi", result)
        self.assertIn("bab el-mandeb", result.lower())
        self.assertIn("commercial shipping", result.lower())
        self.assertIn("fatalities reported", result.lower())
        self.assertNotIn("6", result)
        self.assertNotIn("Outlet", result)
        summary = synthesize_event_summary(headlines, "Israel")
        self.assertIn("numeric details differ", summary.lower())

    def test_synthesized_headline_explains_asset_and_operational_consequence(self):
        headlines = [
            "Missile strike closes airport cargo terminal and delays flights - Global Wire",
            "Airport freight operations halted after missile attack - Local Daily",
        ]
        result = synthesize_event_headline(headlines, "Exampleland")
        self.assertIn("missile attack", result.lower())
        self.assertIn("airport cargo operations", result.lower())
        self.assertIn("services suspended", result.lower())
        self.assertIn("Exampleland", result)

    def test_unknown_event_keeps_story_specific_detail_instead_of_generic_fallback(self):
        headlines = [
            "Rhine water levels fall near Duisburg, forcing barges to reduce cargo loads - Local Wire",
            "Low Rhine levels force freight barges to sail with lighter loads - Trade Journal",
        ]
        result = synthesize_event_headline(headlines, "Germany")
        self.assertIn("Rhine", result)
        self.assertTrue(any(term in result.lower() for term in ("water levels", "low rhine")))
        self.assertTrue(any(term in result.lower() for term in ("barges", "freight")))
        self.assertNotIn("Reported disruption affects", result)
        self.assertNotIn("Local Wire", result)


if __name__ == "__main__":
    unittest.main()
