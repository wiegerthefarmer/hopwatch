import unittest

from hopwatch import globalping, local_mtr
from hopwatch.config import GlobalpingLocation, LocalConfig, Target
from hopwatch.model import Trace, to_prometheus

# Trimmed output of `mtr --json --no-dns --aslookup -c 3 1.1.1.1` (mtr 0.95)
MTR_JSON = """
{"report": {"mtr": {"src": "box", "dst": "1.1.1.1", "tos": 0, "tests": 3, "psize": "64", "bitpattern": "0x00"},
 "hubs": [
  {"count": 1, "host": "192.168.1.1", "ASN": "AS???", "Loss%": 0.0, "Snt": 3, "Last": 0.5, "Avg": 0.6, "Best": 0.4, "Wrst": 0.9, "StDev": 0.2},
  {"count": 2, "host": "???", "ASN": "AS???", "Loss%": 100.0, "Snt": 3, "Last": 0.0, "Avg": 0.0, "Best": 0.0, "Wrst": 0.0, "StDev": 0.0},
  {"count": "3", "host": "1.1.1.1", "ASN": "AS13335", "Loss%": 33.3, "Snt": 3, "Last": 9.1, "Avg": 9.5, "Best": 9.0, "Wrst": 10.2, "StDev": 0.6}
 ]}}
"""

GLOBALPING_MEASUREMENT = {
    "id": "abc",
    "type": "mtr",
    "status": "finished",
    "results": [
        {
            "probe": {"city": "Frankfurt", "country": "DE", "asn": 3320, "network": "Deutsche Telekom AG"},
            "result": {
                "status": "finished",
                "hops": [
                    {"resolvedAddress": "10.0.0.1", "asn": [], "stats": {"min": 0.3, "avg": 0.4, "max": 0.6, "stDev": 0.1, "total": 3, "loss": 0}},
                    {"resolvedAddress": None, "asn": [], "stats": {"min": 0, "avg": 0, "max": 0, "stDev": 0, "total": 3, "loss": 100}},
                    {"resolvedAddress": "1.1.1.1", "asn": [13335], "stats": {"min": 4.1, "avg": 4.5, "max": 5.0, "stDev": 0.3, "total": 3, "loss": 0}},
                ],
            },
        }
    ],
}


class LocalMtrTest(unittest.TestCase):
    def test_parse(self):
        hops = local_mtr.parse(MTR_JSON)
        self.assertEqual([h.number for h in hops], [1, 2, 3])
        self.assertEqual(hops[0].ip, "192.168.1.1")
        self.assertIsNone(hops[0].asn)
        self.assertIsNone(hops[1].ip)
        self.assertIsNone(hops[1].avg_ms)  # no fake 0 ms for silent hops
        self.assertEqual(hops[1].loss_percent, 100.0)
        self.assertEqual(hops[2].asn, "AS13335")
        self.assertEqual(hops[2].worst_ms, 10.2)

    def test_command(self):
        target = Target(name="web", host="example.com", protocol="tcp", port=443, ip_version=4)
        cmd = local_mtr.build_command(target, LocalConfig(cycles=5))
        self.assertEqual(
            cmd,
            ["mtr", "--json", "--no-dns", "--report-cycles", "5", "--tcp", "--port", "443", "-4", "example.com"],
        )


class GlobalpingTest(unittest.TestCase):
    def test_request(self):
        target = Target(name="dns", host="1.1.1.1")
        body = globalping.build_request(target, GlobalpingLocation("gp-fra", "Frankfurt"), 3)
        self.assertEqual(body["type"], "mtr")
        self.assertEqual(body["measurementOptions"], {"protocol": "ICMP", "packets": 3})
        self.assertEqual(body["locations"], [{"magic": "Frankfurt", "limit": 1}])

    def test_parse_measurement(self):
        trace = Trace(source="gp-fra", target="dns", timestamp=0, duration=0)
        globalping.parse_measurement(GLOBALPING_MEASUREMENT, trace)
        self.assertIsNone(trace.error)
        self.assertEqual(trace.probe["asn"], "AS3320")
        self.assertEqual(len(trace.hops), 3)
        self.assertIsNone(trace.hops[1].ip)
        self.assertIsNone(trace.hops[1].avg_ms)
        self.assertEqual(trace.hops[2].asn, "AS13335")
        self.assertEqual(trace.hops[2].best_ms, 4.1)


class PrometheusTest(unittest.TestCase):
    def test_render(self):
        trace = Trace(source="local", target="dns", timestamp=1700000000.5, duration=3.2, hops=local_mtr.parse(MTR_JSON))
        lines = to_prometheus(trace).splitlines()
        self.assertIn('mtr_run_success{source="local",target="dns"} 1 1700000000500', lines)
        self.assertIn('mtr_hop_loss_percent{source="local",target="dns",hop="2",hop_ip="???"} 100.0 1700000000500', lines)
        self.assertIn(
            'mtr_hop_rtt_avg_ms{source="local",target="dns",hop="3",hop_ip="1.1.1.1",asn="AS13335"} 9.5 1700000000500',
            lines,
        )
        self.assertIn('mtr_dest_rtt_avg_ms{source="local",target="dns"} 9.5 1700000000500', lines)
        # silent hop has loss but no latency series
        self.assertFalse(any(l.startswith("mtr_hop_rtt_avg_ms") and 'hop="2"' in l for l in lines))

    def test_error_run(self):
        trace = Trace(source="local", target="dns", timestamp=1, duration=1, error='bad "thing"')
        out = to_prometheus(trace)
        self.assertIn('mtr_run_success{source="local",target="dns"} 0 1000', out)
        self.assertNotIn("mtr_hop_", out)


if __name__ == "__main__":
    unittest.main()
