"""Unit tests for the pure logic. Run: python -m unittest discover -s tests -v"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cities
import run_cycle
import store
from collectors import base as cbase


CFG = {
    "filters": {"require_direct": True,
                "dep_time_window": ["07:00", "22:00"],
                "drop_flights_without_checked_baggage": False},
    "rule": {"baseline_days": 3, "new_low_margin": 30, "hard_cap_cny": None,
             "notify_cooldown_hours": 6, "min_history_hours": 24},
}


def offer(**kw):
    base = {"platform": "t", "flight_no": "MU1", "airline": "x", "dep_time": "08:00",
            "arr_time": "11:00", "from_airport": "A", "to_airport": "B",
            "price": 860, "stops": 0, "url": "u"}
    base.update(kw)
    return base


class TestCities(unittest.TestCase):
    def test_name_expands_to_all_airports(self):
        self.assertEqual(cities.resolve("上海"), ["SHA", "PVG"])

    def test_explicit_code_wins(self):
        self.assertEqual(cities.resolve("上海", "PVG"), ["PVG"])

    def test_bare_code_passes_through(self):
        self.assertEqual(cities.resolve("hak"), ["HAK"])

    def test_city_suffix_is_stripped(self):
        self.assertEqual(cities.resolve("北京市"), ["PEK", "PKX"])

    def test_unknown_city_raises(self):
        with self.assertRaises(KeyError):
            cities.resolve("火星")


class TestPricing(unittest.TestCase):
    FEES = {"fallback_total_cny": 70}

    def test_all_in_price_passes_through(self):
        self.assertEqual(cbase.total_price(offer(price=860), self.FEES), 860)

    def test_bare_fare_gets_fees_from_price_base(self):
        self.assertEqual(
            cbase.total_price(offer(price=670, price_base=670, fees_included=False), self.FEES), 740)

    def test_bare_fare_without_price_base_still_adds_fees(self):
        self.assertEqual(cbase.total_price(offer(price=670, fees_included=False), self.FEES), 740)

    def test_validate_rejects_missing_key(self):
        bad = offer()
        del bad["flight_no"]
        with self.assertRaises(ValueError):
            cbase.validate(bad)

    def test_validate_rejects_non_int_price(self):
        with self.assertRaises(ValueError):
            cbase.validate(offer(price="860"))


class TestFilters(unittest.TestCase):
    def test_acceptable_flight_has_no_reasons(self):
        self.assertEqual(run_cycle.reject_reasons(offer(), CFG), [])

    def test_connection_is_rejected(self):
        self.assertIn("经停1次", run_cycle.reject_reasons(offer(stops=1), CFG))

    def test_red_eye_departure_is_rejected(self):
        reasons = run_cycle.reject_reasons(offer(dep_time="05:40"), CFG)
        self.assertTrue(any("不在可接受时段" in r for r in reasons))

    def test_cross_midnight_is_flagged_but_kept_by_default(self):
        o = offer(dep_time="20:45", arr_time="00:15")
        self.assertEqual(run_cycle.reject_reasons(o, CFG), [])
        self.assertTrue(o["cross_midnight"])

    def test_cross_midnight_can_be_dropped(self):
        cfg = {"filters": dict(CFG["filters"], drop_cross_midnight=True)}
        reasons = run_cycle.reject_reasons(offer(dep_time="20:45", arr_time="00:15"), cfg)
        self.assertTrue(any("跨天到达" in r for r in reasons))


class TestFeeDerivation(unittest.TestCase):
    def test_real_taxes_are_recovered_from_fare_delta(self):
        offers = [offer(price=860, price_base=670, fees_included=True)]
        self.assertEqual(run_cycle.derive_fee(offers), 190)

    def test_estimates_are_not_used_as_observation(self):
        offers = [offer(price=740, price_base=670, fees_included=False)]
        self.assertIsNone(run_cycle.derive_fee(offers))

    def test_no_observation_returns_none(self):
        self.assertIsNone(run_cycle.derive_fee([]))


class TestAlertRule(unittest.TestCase):
    def base(self, **kw):
        data = {"count": 32, "min": 900, "p10": 900, "median": 1000, "span_hours": 30.0}
        data.update(kw)
        return data

    def test_new_low_triggers_after_enough_history(self):
        best, reasons = run_cycle.evaluate([(offer(), 860)], self.base(), CFG)
        self.assertEqual(best[1], 860)
        self.assertTrue(any("新低" in r for r in reasons))

    def test_too_few_samples_does_not_trigger(self):
        _, reasons = run_cycle.evaluate([(offer(), 860)], self.base(count=3), CFG)
        self.assertEqual(reasons, [])

    def test_short_history_span_does_not_trigger(self):
        _, reasons = run_cycle.evaluate([(offer(), 860)], self.base(span_hours=1.0), CFG)
        self.assertEqual(reasons, [])

    def test_hard_cap_triggers_without_history(self):
        cfg = {"filters": CFG["filters"], "rule": dict(CFG["rule"], hard_cap_cny=900)}
        _, reasons = run_cycle.evaluate([(offer(), 860)], self.base(count=0, span_hours=0), cfg)
        self.assertTrue(any("硬上限" in r for r in reasons))

    def test_time_parsing(self):
        self.assertEqual(run_cycle.hhmm("07:35"), 455)
        self.assertIsNone(run_cycle.hhmm("oops"))


class TestStore(unittest.TestCase):
    def setUp(self):
        self._old = store.DB_PATH
        store.DB_PATH = os.path.join(tempfile.mkdtemp(), "t.db")

    def tearDown(self):
        store.DB_PATH = self._old

    def test_baseline_ignores_selftest_rows(self):
        store.add_offers(1000, "SHA-HAK", "2026-12-16",
                         [(offer(platform="selftest", price=615), 615),
                          (offer(platform="tongcheng", price=860), 860)])
        stats = store.baseline("SHA-HAK", "2026-12-16", days=3, now=2000)
        self.assertEqual(stats["count"], 1)
        self.assertEqual(stats["min"], 860)

    def test_route_fee_survives_roundtrip(self):
        store.save_route_fee("SHA-HAK", 1000, 190)
        self.assertEqual(store.get_route_fee("SHA-HAK", now=1000)[0], 190)

    def test_route_fee_is_recovered_from_offer_history(self):
        store.add_offers(1000, "SHA-HAK", "2026-12-16",
                         [(offer(price=860, price_base=670), 860)])
        fee = store.get_route_fee("SHA-HAK", now=1000)
        self.assertEqual(fee[0], 190)
        self.assertEqual(fee[1], 0)

    def test_routes_do_not_share_a_baseline(self):
        store.add_offers(1000, "SHA-HAK", "2026-12-16", [(offer(price=860), 860)])
        store.add_offers(1000, "PEK-SYX", "2026-12-16", [(offer(price=3000), 3000)])
        self.assertEqual(store.baseline("PEK-SYX", "2026-12-16", 3, now=2000)["min"], 3000)


class TestWebhook(unittest.TestCase):
    def test_wecom_payload_shape(self):
        p = run_cycle.webhook_payload("wecom", "t", "m")
        self.assertEqual(p["msgtype"], "text")
        self.assertIn("m", p["text"]["content"])

    def test_serverchan_payload_shape(self):
        p = run_cycle.webhook_payload("serverchan", "t", "m")
        self.assertEqual(p["title"], "t")
        self.assertEqual(p["desp"], "m")

    def test_custom_payload_shape(self):
        p = run_cycle.webhook_payload("custom", "t", "m")
        self.assertEqual(p, {"title": "t", "message": "m"})

    def test_no_webhook_is_not_an_error(self):
        ok, detail = run_cycle.push_webhook({"notify": {"webhook_url": ""}}, "t", "m")
        self.assertFalse(ok)
        self.assertIn("no webhook", detail)

    def test_unreachable_webhook_fails_softly(self):
        ok, detail = run_cycle.push_webhook(
            {"notify": {"webhook_url": "http://127.0.0.1:9/none", "webhook_type": "custom"}}, "t", "m")
        self.assertFalse(ok)
        self.assertTrue(detail)


if __name__ == "__main__":
    unittest.main()