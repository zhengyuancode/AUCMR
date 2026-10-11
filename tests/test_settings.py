import unittest

from config.settings import normalize_httpx_proxy_environment


class SettingsTests(unittest.TestCase):
    def test_normalizes_socks_alias_for_httpx_clients(self):
        environment = {
            "ALL_PROXY": "socks://127.0.0.1:7897/",
            "HTTPS_PROXY": "https://proxy.example:8443",
            "NO_PROXY": "127.0.0.1",
        }
        changed = normalize_httpx_proxy_environment(environment)
        self.assertEqual(changed, ["ALL_PROXY"])
        self.assertEqual(environment["ALL_PROXY"], "socks5://127.0.0.1:7897/")
        self.assertEqual(environment["HTTPS_PROXY"], "https://proxy.example:8443")
        self.assertEqual(environment["NO_PROXY"], "127.0.0.1")


if __name__ == "__main__":
    unittest.main()
