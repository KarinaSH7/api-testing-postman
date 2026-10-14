"""Check the committed collection's structure without external packages.

These checks are deliberately narrower than full Postman schema validation.
The JavaScript assertions must still be run in Postman or Newman.
"""

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class PostmanFileTests(unittest.TestCase):
    def test_collection_has_v21_marker_and_scenarios(self):
        collection = json.loads((ROOT / "postman/crm.postman_collection.json").read_text())
        self.assertEqual(collection["info"]["schema"], "https://schema.getpostman.com/json/collection/v2.1.0/collection.json")
        self.assertEqual(len(collection["item"]), 39)
        self.assertEqual(len({item["name"] for item in collection["item"]}), 39)
        for item in collection["item"]:
            with self.subTest(name=item["name"]):
                self.assertIn(item["request"]["method"], {"GET", "POST", "PATCH", "PUT", "DELETE"})
                self.assertTrue(item["request"]["url"].startswith("{{base_url}}/"))
                self.assertEqual(item["event"][0]["listen"], "test")
                self.assertTrue(any("pm.test" in line for line in item["event"][0]["script"]["exec"]))

    def test_environment_is_local_and_has_no_credentials(self):
        environment = json.loads((ROOT / "postman/local.postman_environment.json").read_text())
        self.assertEqual(environment["_postman_variable_scope"], "environment")
        self.assertEqual(environment["values"], [{"key": "base_url", "value": "http://127.0.0.1:8765", "type": "default", "enabled": True}])


if __name__ == "__main__":
    unittest.main()
