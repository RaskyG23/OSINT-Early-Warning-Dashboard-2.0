import unittest

from app.supply_chain import assess_country, country_mentioned


class CountrySignalAliasTests(unittest.TestCase):
    def test_russian_federation_signal_matches_russia(self):
        self.assertTrue(country_mentioned("Russian Federation", "Russia"))

    def test_city_region_country_signal_matches_country(self):
        self.assertTrue(country_mentioned("Moscow, Moskva, Russia", "Russia"))

    def test_alias_signal_enters_country_assessment(self):
        assessment = assess_country("Russia", [], [{
            "source": "GDACS",
            "country": "Russian Federation",
            "location": "Russian Federation",
            "title": "Forest fires in Russian Federation",
            "summary": "Active wildfire event",
            "severity": "Watch",
            "confidence": 70,
        }])
        self.assertEqual(assessment["evidence_count"], 1)
        self.assertEqual(assessment["sensor_status"], "Provisional")


if __name__ == "__main__":
    unittest.main()
