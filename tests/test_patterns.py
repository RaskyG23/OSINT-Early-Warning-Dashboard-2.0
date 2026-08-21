import json
import tempfile
import unittest
from pathlib import Path

from app import database
from app.collectors import (_attach_hazard_signals, _fintraffic_items, _gfw_last_port, _hazard_kinds,
                            ais_vessel_type)
from app.embeddings import train_ppmi_embeddings
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
from app.supply_chain import (assess_article, assess_country, country_sentiment,
                              country_mentioned, country_supply_chain_relevance,
                              operational_connection, tone_assessment)
from app.recommender import article_key, rank_articles, recommendation_reason
from app.news_taxonomy import classify_general_news
from app.transport import (active_records, flight_estimates, format_duration,
                           cargo_flight_assessment, country_relationship, great_circle_km,
                           likely_commercial_aircraft,
                           likely_commercial_vessel, resolve_aircraft_click, resolve_vessel_click,
                           route_path)
from app.disasters import disaster_details


class PatternRecognitionTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        database.DB_PATH = Path(self.tempdir.name) / "patterns.sqlite"
        database.init_db()

    def test_disaster_details_preserves_gdacs_metadata_and_alert_scale(self):
        signal = {
            "event_type": "Earthquake", "severity": "Critical", "country": "Exampleland",
            "source_url": "https://example.test/report",
            "raw_json": json.dumps({"properties": {
                "eventtype": "EQ", "eventid": 123, "alertlevel": "red",
                "country": "Exampleland", "fromdate": "2026-08-20T10:00:00",
                "iscurrent": True, "severitydata": {
                    "severity": 6.4, "severitytext": "Magnitude 6.4M, Depth:10km"
                }, "url": {"report": "https://example.test/report"},
            }}),
        }
        result = disaster_details(signal)
        self.assertEqual(result["event_type"], "EQ")
        self.assertEqual(result["alert_score"], 3.0)
        self.assertEqual(result["magnitude"], 6.4)
        self.assertTrue(result["is_current"])

    def test_ais_type_codes_are_rendered_as_operational_classes(self):
        self.assertEqual(ais_vessel_type(70), "Cargo")
        self.assertEqual(ais_vessel_type("82"), "Tanker")
        self.assertEqual(ais_vessel_type(60), "Passenger")

    def test_fintraffic_parser_accepts_geojson_and_list_payloads(self):
        feature = {"type": "Feature", "properties": {"mmsi": 123}}
        self.assertEqual(_fintraffic_items({"features": [feature]}), [feature])
        self.assertEqual(_fintraffic_items([{"mmsi": 123}]), [{"mmsi": 123}])

    def test_vessel_voyage_metadata_persists(self):
        database.upsert_vessel_positions([{
            "mmsi": "230000001", "ship_name": "TEST CARRIER", "vessel_type": "Cargo",
            "imo": "9876543", "call_sign": "TEST7", "last_port": "Helsinki",
            "destination": "Tallinn", "eta": "21/08 18:30 UTC", "draught_m": 7.2,
            "latitude": 60.1, "longitude": 24.9, "speed_knots": 12.4,
            "course": 180, "heading": 179, "observed_at": "2026-08-21T10:00:00+00:00",
            "collected_at": "2026-08-21T10:00:01+00:00", "source": "Fintraffic AIS",
            "monitor_country": "Finland",
        }])
        row = database.latest_vessels(10, "Finland")[0]
        self.assertEqual(row["destination"], "Tallinn")
        self.assertEqual(row["vessel_type"], "Cargo")
        self.assertEqual(row["imo"], "9876543")

    def test_current_gdacs_view_expires_events_absent_from_recent_snapshots(self):
        database.upsert_signals([{
            "id": "gdacs-EQ-old", "source": "GDACS", "event_type": "Earthquake",
            "title": "Old event", "location": "Exampleland", "country": "Exampleland",
            "latitude": 1.0, "longitude": 2.0, "severity": "Watch", "confidence": 95,
            "summary": "", "outlook": "", "source_url": "https://example.test",
            "source_name": "GDACS", "observed_at": "2026-08-20T06:00:00+00:00",
            "collected_at": "2026-08-20T06:00:00+00:00", "raw_json": "{}",
        }, {
            "id": "gdacs-FL-live", "source": "GDACS", "event_type": "Flood",
            "title": "Live event", "location": "Exampleland", "country": "Exampleland",
            "latitude": 1.0, "longitude": 2.0, "severity": "High", "confidence": 95,
            "summary": "", "outlook": "", "source_url": "https://example.test",
            "source_name": "GDACS", "observed_at": "2026-08-20T10:00:00+00:00",
            "collected_at": "2026-08-20T10:00:00+00:00", "raw_json": "{}",
        }])
        database.record_run("GDACS", "live", 1, "", "2026-08-20T10:00:00+00:00")
        rows = database.current_gdacs_signals(presence_grace_hours=3)
        self.assertEqual([row["id"] for row in rows], ["gdacs-FL-live"])

    def test_country_targeted_sentiment_labels_operational_damage_negative(self):
        result = country_sentiment(
            "Exampleland's ports reopened. A severe flood damaged Exampleland's main freight corridor.",
            "Exampleland",
        )
        self.assertEqual(result["label"], "Negative")
        self.assertLess(result["score"], 0)
        self.assertTrue(result["target_explicit"])

    def test_general_news_classifier_assigns_multiple_relevant_categories(self):
        result = classify_general_news({
            "headline": "Government budget and central bank interest-rate decision shake markets",
            "summary": "Ministers announced fiscal measures after inflation increased.",
        })
        self.assertIn("Politics and governance", result["labels"])
        self.assertIn("Economy and finance", result["labels"])

    def test_general_news_classifier_recognises_conflict_and_transport_story(self):
        result = classify_general_news({
            "headline": "Missile attack closes cargo port and disrupts shipping routes",
            "summary": "Vessels were diverted following the armed attack.",
        })
        self.assertIn("Conflict and security", result["labels"])
        self.assertIn("Transport and infrastructure", result["labels"])
        self.assertLessEqual(len(result["labels"]), 3)

    def test_general_news_classifier_has_clear_fallback(self):
        result = classify_general_news({"headline": "Museum unveils annual arts exhibition"})
        self.assertEqual(result["labels"], ["General affairs"])

    def test_country_targeted_sentiment_handles_negated_closure(self):
        result = country_sentiment("Exampleland avoided port closure and restored cargo operations.", "Exampleland")
        self.assertEqual(result["label"], "Positive")
        self.assertGreater(result["score"], 0)

    def test_uk_aliases_pass_country_operational_relevance(self):
        for headline in (
            "UK port strike delays container freight",
            "British airport closure disrupts cargo flights",
            "Britain faces shipping delays after terminal outage",
        ):
            article = {"headline": headline, "summary": "", "coverage_scope": "International"}
            result = country_supply_chain_relevance(article, "United Kingdom")
            self.assertTrue(result["relevant"], headline)

    def test_dotted_uk_alias_and_country_sentiment_are_targeted(self):
        self.assertTrue(country_mentioned("U.K. ports reopened after disruption", "United Kingdom"))
        result = country_sentiment("British ports reopened and restored cargo operations.", "United Kingdom")
        self.assertTrue(result["target_explicit"])
        self.assertEqual(result["label"], "Positive")

    def test_short_us_alias_does_not_match_lowercase_pronoun(self):
        self.assertFalse(country_mentioned("The carrier told us its port remained closed.", "United States"))
        self.assertTrue(country_mentioned("US port operations remain suspended.", "United States"))

    def test_common_alternative_country_names_are_recognised(self):
        examples = (
            ("USA port faces delayed container cargo", "United States"),
            ("America imposes new cargo restriction", "United States"),
            ("Holland rail strike disrupts freight", "Netherlands"),
            ("Dutch airport closure delays cargo", "Netherlands"),
            ("Czech Republic border restriction disrupts trucking", "Czechia"),
            ("Burma port outage delays shipping", "Myanmar"),
            ("Cape Verde airport closes after storm", "Cabo Verde"),
            ("DPRK border restriction halts freight", "North Korea"),
        )
        for headline, country in examples:
            self.assertTrue(country_mentioned(headline, country), (headline, country))

    def test_country_aliases_do_not_use_ambiguous_substrings(self):
        self.assertFalse(country_mentioned("The company said a turkey shipment was delayed", "Türkiye"))
        self.assertFalse(country_mentioned("The cargo vessel will congo through repairs", "Congo"))

    def test_zero_result_country_refresh_is_not_treated_as_fresh_cache(self):
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        database.record_country_refresh("United Kingdom", 0, now)
        self.assertFalse(database.country_cache_fresh("United Kingdom"))
        database.record_country_refresh("United Kingdom", 3, now)
        self.assertTrue(database.country_cache_fresh("United Kingdom"))

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

    def test_country_assessment_uses_ten_latest_updates_not_older_high_score(self):
        latest = [{
            "headline": f"Routine port operations continue normally update {index}",
            "summary": "No disruption reported.", "category": "Infrastructure and supply chains",
            "published_at": f"2026-08-{19-index:02d}T10:00:00+00:00", "sources_json": "[]",
        } for index in range(10)]
        old_critical = {
            "headline": "Missile attacks close port and airport, halting freight and shipping",
            "summary": "Infrastructure is damaged and cargo operations are suspended.",
            "category": "Infrastructure and supply chains", "published_at": "2026-07-01T10:00:00+00:00",
            "sources_json": json.dumps([
                {"publisher": "Port Authority", "source_family": "port.test", "source_type": "Port authority"},
                {"publisher": "Independent News", "source_family": "news.test", "source_type": "News reporting"},
            ]),
        }
        result = assess_country("Exampleland", latest + [old_critical], [])
        self.assertNotEqual(result["level"], "Critical")
        self.assertEqual(result["evidence_count"], 10)

    def test_latest_displayed_critical_update_escalates_country(self):
        critical = {
            "headline": "Missile attack closes airport cargo terminal and halts freight",
            "summary": "Airport cargo operations are suspended after physical damage.",
            "category": "Infrastructure and supply chains", "published_at": "2026-08-18T10:00:00+00:00",
            "sources_json": json.dumps([
                {"publisher": "Aviation Authority", "source_family": "authority.test", "source_type": "Aviation authority"},
                {"publisher": "Independent News", "source_family": "news.test", "source_type": "News reporting"},
            ]),
        }
        routine = {"headline": "Routine market update", "summary": "Normal activity.",
                   "category": "Economy and markets", "published_at": "2026-08-17T09:00:00+00:00",
                   "sources_json": "[]"}
        result = assess_country("Exampleland", [critical, routine, routine, routine, routine], [])
        self.assertEqual(result["level"], "Critical")
        self.assertIn("ten latest distinct scoring events", result["escalation_reason"])

    def test_country_cannot_be_low_when_latest_set_has_credible_moderate_event(self):
        moderate = {
            "headline": "Shipping attacks force vessels to reroute from strategic waters",
            "summary": "Cargo routes face disruption.", "category": "Infrastructure and supply chains",
            "published_at": "2026-08-18T10:00:00+00:00",
            "sources_json": json.dumps([
                {"publisher": "Maritime News", "source_family": "one.test", "source_type": "News reporting"},
                {"publisher": "Independent Wire", "source_family": "two.test", "source_type": "News reporting"},
            ]),
        }
        routine = {"headline": "Routine market update", "summary": "Normal activity.",
                   "category": "Economy and markets", "published_at": "2026-08-17T09:00:00+00:00",
                   "sources_json": "[]"}
        result = assess_country("Exampleland", [moderate, routine, routine, routine, routine], [])
        self.assertEqual(result["level"], "Moderate")
        self.assertGreaterEqual(result["score"], 38)

    def test_multiple_distinct_credible_events_strengthen_ten_event_score(self):
        sources = json.dumps([
            {"publisher": "Port Authority", "source_family": "port.test", "source_type": "Port authority"},
            {"publisher": "Independent News", "source_family": "news.test", "source_type": "News reporting"},
        ])
        events = [{
            "headline": f"Port closure delays cargo at terminal {index}",
            "summary": f"Freight handling at terminal {index} is suspended.",
            "category": "Infrastructure and supply chains",
            "published_at": f"2026-08-{19-index:02d}T10:00:00+00:00", "sources_json": sources,
        } for index in range(5)]
        single = assess_country("Exampleland", events[:1], [])
        sustained = assess_country("Exampleland", events, [])
        self.assertEqual(sustained["scoring_window_size"], 5)
        self.assertEqual(sustained["credible_event_count"], 5)
        self.assertGreater(sustained["concurrency_bonus"], 0)
        self.assertGreater(sustained["score"], single["score"])

    def test_country_hybrid_uses_generic_weights_without_company_exposure(self):
        article = {
            "headline": "Port strike closes container terminal and delays cargo",
            "summary": "The terminal suspended freight handling.",
            "category": "Infrastructure and supply chains", "published_at": "2026-08-18T10:00:00+00:00",
            "sources_json": json.dumps([
                {"publisher": "Port Authority", "source_family": "port.test", "source_type": "Port authority"},
                {"publisher": "Independent News", "source_family": "news.test", "source_type": "News reporting"},
            ]),
        }
        result = assess_country("Exampleland", [article], [], anomaly_score=40)
        self.assertEqual(result["model"], "Generic hybrid (company exposure not configured)")
        self.assertFalse(result["exposure_configured"])
        self.assertEqual(set(result["components"]), {"credible_event", "likelihood_impact", "historical_anomaly"})
        self.assertAlmostEqual(sum(result["component_weights"].values()), 1.0, places=3)

    def test_company_exposure_is_a_weighted_hybrid_component(self):
        article = {
            "headline": "Port strike closes container terminal and delays cargo",
            "summary": "The terminal suspended freight handling.",
            "category": "Infrastructure and supply chains", "published_at": "2026-08-18T10:00:00+00:00",
            "sources_json": json.dumps([
                {"publisher": "Port Authority", "source_family": "port.test", "source_type": "Port authority"},
                {"publisher": "Independent News", "source_family": "news.test", "source_type": "News reporting"},
            ]),
        }
        exposure = {
            "supplier_concentration": 100, "goods_value": 80, "route_dependency": 60,
            "inventory_vulnerability": 40, "customer_exposure": 20, "substitution_difficulty": 10,
        }
        result = assess_country("Exampleland", [article], [], exposure=exposure, anomaly_score=25)
        self.assertEqual(result["model"], "Exposure-aware hybrid")
        self.assertTrue(result["exposure_configured"])
        self.assertEqual(result["components"]["company_exposure"], 62)
        self.assertEqual(result["component_weights"]["company_exposure"], .30)

    def test_company_exposure_persists_per_profile_and_country(self):
        exposure = {
            "supplier_concentration": 75, "goods_value": 60, "route_dependency": 85,
            "inventory_vulnerability": 45, "customer_exposure": 30, "substitution_difficulty": 55,
        }
        database.save_country_exposure("Team A", "Exampleland", exposure)
        stored = database.profile_country_exposures("Team A")
        self.assertEqual(stored["Exampleland"]["route_dependency"], 85)
        self.assertEqual(database.profile_country_exposures("Team B"), {})

    def test_operational_connection_requires_asset_consequence_and_confirmation(self):
        confirmed = {
            "country": "Netherlands",
            "headline": "Rotterdam Port closes container terminal as strike delays cargo",
            "summary": "The ongoing closure suspends freight handling in the Netherlands.",
            "sources_json": json.dumps([
                {"publisher": "Port Authority", "source_family": "port.test", "source_type": "Port authority"},
                {"publisher": "Independent News", "source_family": "news.test", "source_type": "News reporting"},
            ]),
        }
        result = operational_connection(confirmed, {"route_dependency": 90})
        self.assertTrue(result["gate_passed"])
        self.assertIn("Rotterdam Port", result["assets"])
        self.assertIn("closes", result["consequences"])
        self.assertEqual(result["lifecycle"], "Active")
        self.assertEqual(result["components"]["source_support"], 100)
        self.assertEqual(result["components"]["company_dependency"], 18)

    def test_generic_war_report_fails_operational_evidence_gate(self):
        report = {
            "country": "Exampleland", "headline": "War tensions rise in Exampleland",
            "summary": "Officials discuss the conflict.",
            "sources_json": json.dumps([
                {"publisher": "News A", "source_family": "a.test", "source_type": "News reporting"},
                {"publisher": "News B", "source_family": "b.test", "source_type": "News reporting"},
            ]),
        }
        result = operational_connection(report)
        self.assertFalse(result["gate_passed"])
        self.assertIn("identifiable asset, route or location", result["missing_evidence"])
        self.assertIn("concrete operational consequence", result["missing_evidence"])
        self.assertLessEqual(result["score"], 59)

    def test_resumption_marks_event_as_recovering(self):
        report = {
            "country": "Exampleland", "headline": "Exampleland airport reopens",
            "summary": "Cargo flights resumed after the closure was cleared.",
            "sources_json": "[]",
        }
        self.assertEqual(operational_connection(report)["lifecycle"], "Recovering / resolved")

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

    def test_primary_source_status_cannot_bypass_country_identification(self):
        unrelated = {"headline": "Global LNG prices rise after shipping disruption",
                     "summary": "A primary operational bulletin.",
                     "coverage_scope": "Primary operational"}
        self.assertFalse(country_supply_chain_relevance(unrelated, "Libya")["relevant"])

    def test_tone_assessment_distinguishes_negative_and_positive_reporting(self):
        self.assertEqual(tone_assessment("Attack closes port and delays shipping")["label"], "Negative")
        self.assertEqual(tone_assessment("Port reopens and freight service resumes")["label"], "Positive")

    def test_event_anchor_matching_links_reworded_maritime_attack(self):
        long = "Shipping attacks escalate; 6 killed in Houthi attack in Bab el-Mandeb"
        short = "Pakistan says 3 citizens killed in Houthi attack on ship in Bab el-Mandeb"
        self.assertTrue(event_match(long, short))
        self.assertIn("Numeric details differ", fact_variance(long, short))

    def test_same_named_country_incident_clusters_across_assets_and_categories(self):
        refinery = "Drone attacks damage Zawiya refinery and threaten Libya oil production"
        substation = "Drone strike burns Zawiya power substation, triggering outages in Libya"
        unrelated = "Dockworkers strike closes Tripoli container terminal in Libya"
        self.assertTrue(event_match(refinery, substation))
        self.assertFalse(event_match(refinery, unrelated))

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

    def test_content_preferences_promote_similar_country_story(self):
        articles = [
            {"headline": "Port strike delays container shipping", "summary": "Cargo backlog grows.",
             "country": "Exampleland", "country_relevance_score": 90,
             "published_at": "2026-08-13T10:00:00+00:00", "risk": {"score": 60, "confidence": 70}},
            {"headline": "Airport runway closes after storm", "summary": "Flights are cancelled.",
             "country": "Exampleland", "country_relevance_score": 90,
             "published_at": "2026-08-13T10:00:00+00:00", "risk": {"score": 60, "confidence": 70}},
        ]
        feedback = [{"headline": "Dockworkers halt port container operations", "summary": "Shipping delayed",
                     "country": "Otherland", "transport_mode": "Maritime", "feedback": 1}]
        ranked = rank_articles(articles, feedback)
        self.assertEqual(ranked[0]["headline"], articles[0]["headline"])
        self.assertGreater(ranked[0]["recommendation_score"], ranked[1]["recommendation_score"])
        self.assertIn("Matches your interest", recommendation_reason(ranked[0], feedback))

    def test_negative_feedback_demotes_similar_story(self):
        articles = [
            {"headline": "Port strike delays shipping", "country": "Exampleland", "country_relevance_score": 90,
             "published_at": "2026-08-13T10:00:00+00:00", "risk": {"score": 50, "confidence": 60}},
            {"headline": "Airport closure cancels cargo flights", "country": "Exampleland", "country_relevance_score": 90,
             "published_at": "2026-08-13T10:00:00+00:00", "risk": {"score": 50, "confidence": 60}},
        ]
        feedback = [{"headline": "Port workers strike disrupts shipping", "transport_mode": "Maritime", "feedback": -1}]
        ranked = rank_articles(articles, feedback)
        self.assertEqual(ranked[0]["headline"], articles[1]["headline"])

    def test_hybrid_recommender_pins_supported_critical_warning(self):
        articles = [
            {"headline": "Warehouse software upgrade improves inventory planning", "country": "Exampleland",
             "country_relevance_score": 90, "published_at": "2026-08-13T10:00:00+00:00",
             "risk": {"level": "Low", "score": 10, "confidence": 80}},
            {"headline": "Attack destroys airport cargo terminal and suspends freight", "country": "Exampleland",
             "country_relevance_score": 90, "published_at": "2026-08-13T09:00:00+00:00",
             "risk": {"level": "Critical", "score": 85, "confidence": 75}},
        ]
        feedback = [{"headline": "Warehouse technology improves inventory", "feedback": 1}]
        ranked = rank_articles(articles, feedback)
        self.assertEqual(ranked[0]["risk"]["level"], "Critical")

    def test_hybrid_classifier_activates_with_positive_and_negative_examples(self):
        articles = [
            {"headline": "Port strike delays container shipping", "country": "Exampleland",
             "country_relevance_score": 90, "published_at": "2026-08-13T10:00:00+00:00",
             "risk": {"level": "Moderate", "score": 55, "confidence": 65}},
        ]
        feedback = [
            {"headline": "Port closure disrupts cargo shipping", "feedback": 1},
            {"headline": "Container terminal strike delays vessels", "feedback": 1},
            {"headline": "Airport passenger lounge renovation", "feedback": -1},
            {"headline": "Tourism festival increases passenger flights", "feedback": -1},
        ]
        ranked = rank_articles(articles, feedback)
        self.assertEqual(ranked[0]["recommendation_model"], "Hybrid semantic + online logistic")
        self.assertIn("learned_interest_score", ranked[0])

    def test_feedback_is_isolated_by_profile(self):
        article = {"country": "Exampleland", "headline": "Port closure delays cargo", "summary": "Delay",
                   "category": "Supply chain", "transport_mode": "Maritime", "url": "https://example.test/a"}
        key = article_key(article)
        database.save_article_feedback("Maritime", key, article, 1)
        database.save_article_feedback("Aviation", key, article, -1)
        self.assertEqual(database.feedback_for_articles("Maritime", [key])[key], 1)
        self.assertEqual(database.feedback_for_articles("Aviation", [key])[key], -1)

    def test_transport_activity_filters_stale_and_non_operational_positions(self):
        from datetime import datetime, timezone
        records = [
            {"observed_at": "2026-08-13T09:50:00+00:00", "callsign": "BAW123", "on_ground": 0},
            {"observed_at": "2026-08-13T08:00:00+00:00", "callsign": "OLD123", "on_ground": 0},
        ]
        recent = active_records(records, 30, now=datetime(2026, 8, 13, 10, 0, tzinfo=timezone.utc))
        self.assertEqual(len(recent), 1)
        self.assertTrue(likely_commercial_aircraft(recent[0]))
        self.assertFalse(likely_commercial_aircraft({"callsign": "BAW123", "on_ground": 1}))

    def test_vessel_commercial_classification_prefers_ais_type(self):
        self.assertTrue(likely_commercial_vessel({"vessel_type": "Cargo", "ship_name": "", "speed_knots": 0}))
        self.assertTrue(likely_commercial_vessel({"vessel_type": "", "ship_name": "SEA STAR", "speed_knots": 8}))
        self.assertFalse(likely_commercial_vessel({"vessel_type": "", "ship_name": "MMSI 123", "speed_knots": 8}))

    def test_flight_route_estimates_use_route_and_live_position(self):
        from datetime import datetime, timezone
        route = {"origin_latitude": 51.47, "origin_longitude": -0.45,
                 "destination_latitude": 40.64, "destination_longitude": -73.78}
        position = {"latitude": 50.0, "longitude": -20.0, "velocity_knots": 480}
        result = flight_estimates(position, route, now=datetime(2026, 8, 13, 10, 0, tzinfo=timezone.utc))
        self.assertGreater(result["route_km"], 5000)
        self.assertGreater(result["remaining_km"], 3000)
        self.assertGreater(result["duration_minutes"], 400)
        self.assertTrue(format_duration(result["duration_minutes"]).startswith("7h"))
        self.assertGreater(result["eta"], datetime(2026, 8, 13, 10, 0, tzinfo=timezone.utc))
        self.assertAlmostEqual(great_circle_km(51.47, -0.45, 51.47, -0.45), 0)

    def test_flight_route_cache_round_trip(self):
        route = {"callsign": "BAW123", "airline": "British Airways", "origin_name": "Heathrow",
                 "origin_iata": "LHR", "origin_icao": "EGLL", "origin_latitude": 51.47,
                 "origin_longitude": -0.45, "destination_name": "JFK", "destination_iata": "JFK",
                 "destination_icao": "KJFK", "destination_latitude": 40.64,
                 "destination_longitude": -73.78, "source": "ADSBDB", "status": "matched",
                 "checked_at": "2026-08-13T10:00:00+00:00"}
        database.upsert_flight_route(route)
        stored = database.flight_routes(["BAW123"])["BAW123"]
        self.assertEqual(stored["origin_iata"], "LHR")
        self.assertEqual(stored["destination_iata"], "JFK")

    def test_country_relationship_labels_route_or_transit(self):
        departing = {"origin_country": "United Kingdom", "destination_country": "United States"}
        arriving = {"origin_country": "France", "destination_country": "United Kingdom"}
        self.assertEqual(country_relationship(departing, "United Kingdom"), "Departing from selected country")
        self.assertEqual(country_relationship(arriving, "United Kingdom"), "Arriving in selected country")
        self.assertEqual(country_relationship({}, "United Kingdom", True),
                         "Transiting selected-country monitoring area")
        self.assertIsNone(country_relationship({}, "United Kingdom", False))

    def test_aircraft_click_resolves_by_index_or_coordinates(self):
        aircraft = [
            {"icao24": "aaa111", "latitude": 51.0, "longitude": -1.0},
            {"icao24": "bbb222", "latitude": 52.0, "longitude": -2.0},
        ]
        self.assertEqual(resolve_aircraft_click({"pointNumber": 1}, aircraft)["icao24"], "bbb222")
        self.assertEqual(resolve_aircraft_click({"lat": 51.01, "lon": -1.01}, aircraft)["icao24"], "aaa111")
        self.assertIsNone(resolve_aircraft_click({}, aircraft))

    def test_vessel_click_prefers_mmsi_customdata(self):
        vessels = [
            {"mmsi": "111000111", "latitude": 60.0, "longitude": 20.0},
            {"mmsi": "222000222", "latitude": 61.0, "longitude": 21.0},
        ]
        self.assertEqual(resolve_vessel_click({"customdata": "222000222", "pointNumber": 0}, vessels)["mmsi"],
                         "222000222")
        self.assertEqual(resolve_vessel_click({"lat": 60.01, "lon": 20.01}, vessels)["mmsi"],
                         "111000111")

    def test_gfw_port_label_handles_current_event_shapes(self):
        self.assertEqual(_gfw_last_port({"portVisit": {"portName": "Rotterdam"}}), "Rotterdam")
        self.assertEqual(_gfw_last_port({"regions": {"namedAnchorage": {"name": "Singapore"}}}),
                         "Singapore")

    def test_cargo_flight_requires_dedicated_operator_evidence(self):
        fedex = cargo_flight_assessment({"callsign": "FDX123"}, {})
        generic = cargo_flight_assessment({"callsign": "BAW123"}, {"airline": "British Airways"})
        named = cargo_flight_assessment({"callsign": "XYZ123"}, {"airline": "Example Cargo Express"})
        self.assertTrue(fedex["cargo"])
        self.assertTrue(named["cargo"])
        self.assertFalse(generic["cargo"])

    def test_hazard_news_attaches_applicable_official_detection(self):
        article = {"headline": "Earthquake damages roads in Exampleland",
                   "summary": "Freight routes disrupted", "sources_json": "[]"}
        signals = [{"country": "Exampleland", "location": "Exampleland", "source": "USGS",
                    "event_type": "Seismic", "title": "Magnitude 6 earthquake in Exampleland",
                    "summary": "USGS recorded an earthquake", "source_url": "https://usgs.test/event"},
                   {"country": "Otherland", "location": "Otherland", "source": "GDACS",
                    "event_type": "Flood", "title": "Flood in Otherland", "summary": "Flood"}]
        result = _attach_hazard_signals(article, "Exampleland", signals)
        sources = json.loads(result["sources_json"])
        self.assertEqual([source["publisher"] for source in sources], ["USGS"])
        self.assertIn("Officially detected", result["hazard_status"])
        self.assertIn("earthquake", _hazard_kinds(article["headline"]))

    def test_country_relevance_keeps_concrete_natural_hazard_without_transport_word(self):
        article = {
            "headline": "Major earthquake strikes Exampleland",
            "summary": "Strong shaking damages buildings and interrupts electricity",
            "coverage_scope": "International",
        }
        result = country_supply_chain_relevance(article, "Exampleland")
        self.assertTrue(result["relevant"])
        self.assertGreaterEqual(result["score"], 60)

    def test_ppmi_embedding_trains_on_stored_style_news_corpus(self):
        documents = []
        for _ in range(12):
            documents.extend([
                "port closure delays cargo freight and disrupts logistics operations",
                "terminal shutdown blocks shipping and causes container shortage",
                "earthquake emergency damages roads and interrupts freight operations",
                "airport closure delays cargo flights after an attack",
            ])
        model = train_ppmi_embeddings(documents)
        related = model.scores("terminal damage interrupts shipping cargo")
        unrelated = model.scores("cultural exhibition celebrates traditional painting")
        self.assertTrue(model.trained)
        self.assertGreater(related["coverage"], unrelated["coverage"])
        self.assertGreater(related["disruption"], 0)

    def test_route_path_uses_origin_live_position_and_destination(self):
        position = {"latitude": 50, "longitude": -20}
        route = {"origin_latitude": 51.47, "origin_longitude": -.45,
                 "destination_latitude": 40.64, "destination_longitude": -73.78}
        path = route_path(position, route)
        self.assertEqual(path["latitudes"], [51.47, 50.0, 40.64])
        self.assertEqual(path["longitudes"], [-.45, -20.0, -73.78])
        self.assertIsNone(route_path(position, {}))

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

    def test_attack_without_concrete_asset_keeps_event_specific_details(self):
        headlines = [
            "Armed attack near Apatzingan in Michoacan kills drivers and delays regional deliveries - Local Wire",
            "Fatal attack outside Apatzingan disrupts deliveries across western Michoacan - National Daily",
        ]
        result = synthesize_event_headline(headlines, "Mexico")
        self.assertIn("Apatzingan", result)
        self.assertIn("Michoacan", result)
        self.assertTrue(any(term in result.lower() for term in ("drivers", "deliveries")))
        self.assertNotIn("supply-chain operations affecting Mexico", result)

    def test_synthesis_never_emits_generic_country_attack_template(self):
        result = synthesize_event_headline(
            ["Attack causes fatalities and delays in Mexico - Example News"], "Mexico"
        )
        self.assertEqual(result, "Attack causes fatalities and delays in Mexico")
        self.assertNotIn("Attack targets supply-chain operations", result)


if __name__ == "__main__":
    unittest.main()
