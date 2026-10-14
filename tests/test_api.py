"""Exercise real HTTP requests against an ephemeral localhost server."""

import concurrent.futures
import http.client
import json
import threading
import unittest

from app import MAX_BODY_BYTES, SEED_CONTACTS, make_server


UNSET = object()
VALID = {"name": "Jordan Blake", "email": "jordan@example.com", "company": "Oak Labs"}


class APITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = make_server(0)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        self.server.store.reset()

    def request(self, method, path, data=UNSET, raw=None, content_type="application/json"):
        body = json.dumps(data).encode() if data is not UNSET else raw
        headers = {"Content-Type": content_type} if content_type is not None else {}
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            payload = response.read()
            self.assertTrue(response.getheader("Content-Type").startswith("application/json"))
            self.assertEqual(int(response.getheader("Content-Length")), len(payload))
            return response.status, json.loads(payload), dict(response.getheaders())
        finally:
            connection.close()

    def assert_error(self, method, path, status, code, **kwargs):
        actual, body, _ = self.request(method, path, **kwargs)
        self.assertEqual(actual, status)
        self.assertEqual(body["error"]["code"], code)
        self.assertIsInstance(body["error"]["message"], str)
        return body

    def test_health(self):
        status, body, _ = self.request("GET", "/health")
        self.assertEqual(status, 200)
        self.assertEqual(body, {"status": "ok", "storage": "memory"})

    def test_default_list_has_stable_order_and_total(self):
        status, body, _ = self.request("GET", "/contacts")
        self.assertEqual(status, 200)
        self.assertEqual(body["data"], SEED_CONTACTS)
        self.assertEqual(body["meta"], {"total": 3, "limit": 20, "offset": 0})

    def test_get_one_contact(self):
        status, body, _ = self.request("GET", "/contacts/1")
        self.assertEqual(status, 200)
        self.assertEqual(body["data"], SEED_CONTACTS[0])

    def test_filter_status(self):
        for expected_id, state in [(1, "lead"), (2, "active"), (3, "inactive")]:
            with self.subTest(state=state):
                status, body, _ = self.request("GET", f"/contacts?status={state}")
                self.assertEqual(status, 200)
                self.assertEqual([c["id"] for c in body["data"]], [expected_id])
                self.assertEqual(body["meta"]["total"], 1)

    def test_pagination(self):
        status, body, _ = self.request("GET", "/contacts?limit=1&offset=1")
        self.assertEqual(status, 200)
        self.assertEqual([c["id"] for c in body["data"]], [2])
        self.assertEqual(body["meta"], {"total": 3, "limit": 1, "offset": 1})

    def test_pagination_maximum_and_empty_page(self):
        status, body, _ = self.request("GET", "/contacts?limit=50&offset=1000000")
        self.assertEqual(status, 200)
        self.assertEqual(body["data"], [])
        self.assertEqual(body["meta"]["total"], 3)

    def test_invalid_query_values(self):
        for query in ["limit=0", "limit=51", "limit=-1", "limit=1.5", "limit=true", "limit=",
                      "offset=-1", "offset=1000001", "offset=no", "status=unknown", "status=",
                      "page=1", "limit=1&limit=2", "limit=1&limit=1"]:
            with self.subTest(query=query):
                self.assert_error("GET", "/contacts?" + query, 422, "validation_error")

    def test_queries_rejected_on_other_routes(self):
        for method, path, data in [("GET", "/health?extra=1", UNSET),
                                    ("GET", "/contacts/1?limit=1", UNSET),
                                    ("POST", "/contacts?extra=1", VALID),
                                    ("POST", "/test/reset?extra=1", {})]:
            with self.subTest(path=path):
                self.assert_error(method, path, 422, "validation_error", data=data)

    def test_create_defaults_and_location(self):
        status, body, headers = self.request("POST", "/contacts", VALID)
        self.assertEqual(status, 201)
        self.assertEqual(body["data"], {"id": 4, **VALID, "status": "lead"})
        self.assertEqual(headers["Location"], "/contacts/4")
        self.assertEqual(self.request("GET", "/contacts/4")[1], body)

    def test_create_normalizes_email_and_whitespace(self):
        status, body, _ = self.request("POST", "/contacts", {
            "name": "  Jo  ", "email": " JO@EXAMPLE.COM ", "company": " Elm ", "status": "active "})
        self.assertEqual(status, 201)
        self.assertEqual(body["data"], {"id": 4, "name": "Jo", "email": "jo@example.com", "company": "Elm", "status": "active"})

    def test_unicode_name_round_trip(self):
        status, body, _ = self.request("POST", "/contacts", {**VALID, "name": "Zoë Яна"})
        self.assertEqual(status, 201)
        self.assertEqual(body["data"]["name"], "Zoë Яна")

    def test_missing_required_fields(self):
        for field in ["name", "email", "company"]:
            with self.subTest(field=field):
                data = dict(VALID)
                del data[field]
                body = self.assert_error("POST", "/contacts", 422, "validation_error", data=data)
                self.assertIn(field, body["error"]["details"])

    def test_wrong_field_types(self):
        for field in ["name", "email", "company", "status"]:
            for value in [None, 12, True, [], {}]:
                with self.subTest(field=field, value=value):
                    self.assert_error("POST", "/contacts", 422, "validation_error", data={**VALID, field: value})

    def test_empty_and_whitespace_fields(self):
        for field in ["name", "email", "company", "status"]:
            for value in ["", "   "]:
                with self.subTest(field=field, value=value):
                    self.assert_error("POST", "/contacts", 422, "validation_error", data={**VALID, field: value})

    def test_length_boundaries_for_name_and_company(self):
        for field, maximum in [("name", 80), ("company", 120)]:
            for length in [1, maximum, maximum + 1]:
                with self.subTest(field=field, length=length):
                    self.server.store.reset()
                    status, body, _ = self.request("POST", "/contacts", {**VALID, field: "x" * length})
                    self.assertEqual(status, 201 if length <= maximum else 422)
                    if status == 201:
                        self.assertEqual(len(body["data"][field]), length)

    def test_email_length_boundary(self):
        # This intentionally checks only the demo's basic email rule, not all email standards.
        for length in [254, 255]:
            with self.subTest(length=length):
                email = "a" * (length - len("@example.com")) + "@example.com"
                status, _, _ = self.request("POST", "/contacts", {**VALID, "email": email})
                self.assertEqual(status, 201 if length == 254 else 422)

    def test_invalid_email_format(self):
        for email in ["plain", "missing@dot", "@example.com", "a@@example.com", "a b@example.com"]:
            with self.subTest(email=email):
                self.assert_error("POST", "/contacts", 422, "validation_error", data={**VALID, "email": email})

    def test_invalid_status(self):
        for status in ["Lead", "customer", "lost"]:
            with self.subTest(status=status):
                self.assert_error("POST", "/contacts", 422, "validation_error", data={**VALID, "status": status})

    def test_unknown_field_is_rejected(self):
        body = self.assert_error("POST", "/contacts", 422, "validation_error", data={**VALID, "id": 99})
        self.assertEqual(body["error"]["details"]["unknown_fields"], ["id"])

    def test_duplicate_email_case_insensitive(self):
        self.assert_error("POST", "/contacts", 409, "duplicate_email",
                          data={**VALID, "email": " ALEX@EXAMPLE.COM "})
        self.assertEqual(self.request("GET", "/contacts")[1]["meta"]["total"], 3)
        self.assertEqual(self.request("POST", "/contacts", VALID)[1]["data"]["id"], 4)

    def test_patch_only_changes_provided_field(self):
        status, body, _ = self.request("PATCH", "/contacts/1", {"status": "active"})
        self.assertEqual(status, 200)
        self.assertEqual(body["data"], {**SEED_CONTACTS[0], "status": "active"})

    def test_patch_own_email_is_allowed(self):
        status, body, _ = self.request("PATCH", "/contacts/1", {"email": " ALEX@EXAMPLE.COM "})
        self.assertEqual(status, 200)
        self.assertEqual(body["data"], SEED_CONTACTS[0])

    def test_duplicate_patch_is_atomic(self):
        self.assert_error("PATCH", "/contacts/1", 409, "duplicate_email",
                          data={"name": "Changed", "email": "sam@example.com"})
        self.assertEqual(self.request("GET", "/contacts/1")[1]["data"], SEED_CONTACTS[0])

    def test_invalid_patch_is_atomic(self):
        self.assert_error("PATCH", "/contacts/1", 422, "validation_error",
                          data={"name": "Changed", "status": "bad"})
        self.assertEqual(self.request("GET", "/contacts/1")[1]["data"], SEED_CONTACTS[0])

    def test_empty_patch_is_rejected(self):
        self.assert_error("PATCH", "/contacts/1", 422, "validation_error", data={})

    def test_delete_and_repeated_delete(self):
        status, body, _ = self.request("DELETE", "/contacts/1")
        self.assertEqual(status, 200)
        self.assertEqual(body, {"deleted": 1})
        self.assert_error("GET", "/contacts/1", 404, "not_found")
        self.assert_error("DELETE", "/contacts/1", 404, "not_found")
        self.assertEqual(self.request("POST", "/contacts", VALID)[1]["data"]["id"], 4)

    def test_missing_contact_for_supported_methods(self):
        for method, data in [("GET", UNSET), ("PATCH", {"name": "No one"}), ("DELETE", UNSET)]:
            with self.subTest(method=method):
                self.assert_error(method, "/contacts/999", 404, "not_found", data=data)

    def test_invalid_routes(self):
        for path in ["/missing", "/contacts/0", "/contacts/-1", "/contacts/no", "/contacts/01", "/contacts/1/extra"]:
            with self.subTest(path=path):
                self.assert_error("GET", path, 404, "not_found")

    def test_unsupported_methods_on_known_routes(self):
        for method, path in [("PUT", "/contacts/1"), ("POST", "/contacts/1"), ("DELETE", "/contacts"),
                              ("POST", "/health"), ("GET", "/test/reset")]:
            with self.subTest(method=method, path=path):
                self.assert_error(method, path, 405, "method_not_allowed")

    def test_malformed_json(self):
        for raw in [b"{", b"", b'{"name":}', b"\xff"]:
            with self.subTest(raw=raw):
                self.assert_error("POST", "/contacts", 400, "invalid_json", raw=raw)

    def test_non_object_json(self):
        for data in [[], None, "hello", 42, True]:
            with self.subTest(data=data):
                self.assert_error("POST", "/contacts", 422, "validation_error", data=data)

    def test_wrong_or_missing_content_type(self):
        for content_type in ["text/plain", None]:
            with self.subTest(content_type=content_type):
                self.assert_error("POST", "/contacts", 415, "unsupported_media_type", data=VALID, content_type=content_type)

    def test_json_content_type_with_charset(self):
        self.assertEqual(self.request("POST", "/contacts", VALID, content_type="application/json; charset=utf-8")[0], 201)

    def test_oversized_body(self):
        self.assert_error("POST", "/contacts", 413, "body_too_large", raw=b" " * (MAX_BODY_BYTES + 1))

    def test_reset_restores_fixture_and_next_id(self):
        self.request("POST", "/contacts", VALID)
        self.request("DELETE", "/contacts/1")
        self.request("PATCH", "/contacts/2", {"status": "inactive"})
        status, body, _ = self.request("POST", "/test/reset", {})
        self.assertEqual(status, 200)
        self.assertEqual(body, {"reset": True, "count": 3})
        self.assertEqual(self.request("GET", "/contacts")[1]["data"], SEED_CONTACTS)
        self.assertEqual(self.request("POST", "/contacts", VALID)[1]["data"]["id"], 4)

    def test_reset_rejects_nonempty_or_nonobject_body(self):
        for data in [{"confirm": True}, [], None, ""]:
            with self.subTest(data=data):
                self.assert_error("POST", "/test/reset", 422, "validation_error", data=data)

    def test_simultaneous_duplicate_creates(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.request("POST", "/contacts", VALID)[0], range(4)))
        self.assertEqual(sorted(results), [201, 409, 409, 409])
        self.assertEqual(self.request("GET", "/contacts")[1]["meta"]["total"], 4)


if __name__ == "__main__":
    unittest.main()
