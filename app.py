"""A deliberately local, in-memory CRM API for practicing API testing."""

import argparse
import copy
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit


STATUSES = {"lead", "active", "inactive"}
FIELDS = {"name", "email", "company", "status"}
MAX_BODY_BYTES = 16_384
SEED_CONTACTS = [
    {"id": 1, "name": "Alex Morgan", "email": "alex@example.com", "company": "North Studio", "status": "lead"},
    {"id": 2, "name": "Sam Rivera", "email": "sam@example.com", "company": "Cedar Works", "status": "active"},
    {"id": 3, "name": "Taylor Reed", "email": "taylor@example.com", "company": "Maple Design", "status": "inactive"},
]


class APIError(Exception):
    def __init__(self, status, code, message, details=None):
        super().__init__(message)
        self.status = status
        self.payload = {"error": {"code": code, "message": message}}
        if details:
            self.payload["error"]["details"] = details


class ContactStore:
    """One server's data. A lock keeps duplicate checking and writes together."""

    def __init__(self):
        self.lock = threading.RLock()
        self.reset()

    def reset(self):
        with self.lock:
            self.contacts = {contact["id"]: copy.deepcopy(contact) for contact in SEED_CONTACTS}
            self.next_id = 4


def validate_contact(data, partial=False):
    if not isinstance(data, dict):
        raise APIError(422, "validation_error", "The JSON body must be an object.")
    if partial and not data:
        raise APIError(422, "validation_error", "Provide at least one field.")
    errors = {}
    unknown = sorted(set(data) - FIELDS)
    if unknown:
        errors["unknown_fields"] = unknown
    required = {"name", "email", "company"}
    if not partial:
        for field in sorted(required - set(data)):
            errors[field] = "This field is required."
    result = {}
    for field in sorted(FIELDS & set(data)):
        value = data[field]
        if not isinstance(value, str):
            errors[field] = "Use a string."
            continue
        value = value.strip()
        maximum = {"name": 80, "email": 254, "company": 120, "status": 8}[field]
        if not 1 <= len(value) <= maximum:
            errors[field] = f"Use between 1 and {maximum} characters after trimming."
        elif field == "email" and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
            errors[field] = "Use an email address with a local part and domain."
        elif field == "status" and value not in STATUSES:
            errors[field] = "Use lead, active, or inactive."
        else:
            result[field] = value.lower() if field == "email" else value
    if errors:
        raise APIError(422, "validation_error", "Check the supplied fields.", errors)
    if not partial:
        result.setdefault("status", "lead")
    return result


class CRMHandler(BaseHTTPRequestHandler):
    # HTTP/1.0 closes each connection, simplifying this small teaching server.
    server_version = "CRMPractice/1.0"

    def log_message(self, format, *args):
        pass

    def respond(self, status, payload, headers=None):
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(raw)

    def read_json(self):
        if self.headers.get("Transfer-Encoding"):
            raise APIError(400, "invalid_body", "Transfer-Encoding is not supported in this demo.")
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            raise APIError(415, "unsupported_media_type", "Use Content-Type application/json.")
        length_header = self.headers.get("Content-Length")
        if length_header is None or not re.fullmatch(r"[0-9]+", length_header):
            raise APIError(400, "invalid_body", "Provide a valid Content-Length.")
        length = int(length_header)
        if length > MAX_BODY_BYTES:
            raise APIError(413, "body_too_large", "The request body exceeds 16384 bytes.")
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise APIError(400, "invalid_json", "Provide a valid UTF-8 JSON body.") from None

    def dispatch(self):
        try:
            self.route()
        except APIError as error:
            self.respond(error.status, error.payload)

    do_GET = dispatch
    do_POST = dispatch
    do_PATCH = dispatch
    do_DELETE = dispatch
    do_PUT = dispatch

    def route(self):
        url = urlsplit(self.path)
        path, method = url.path, self.command
        store = self.server.store
        if path == "/health":
            if method != "GET":
                raise APIError(405, "method_not_allowed", "Use GET for this route.")
            if url.query:
                raise APIError(422, "validation_error", "This route accepts no query parameters.")
            self.respond(200, {"status": "ok", "storage": "memory"})
            return
        if path == "/test/reset":
            if method != "POST":
                raise APIError(405, "method_not_allowed", "Use POST for this route.")
            if url.query:
                raise APIError(422, "validation_error", "This route accepts no query parameters.")
            data = self.read_json()
            if data != {}:
                raise APIError(422, "validation_error", "Reset requires an empty JSON object.")
            store.reset()
            self.respond(200, {"reset": True, "count": 3})
            return
        match = re.fullmatch(r"/contacts/([1-9][0-9]*)", path)
        if path != "/contacts" and not match:
            raise APIError(404, "not_found", "Route not found.")
        if path == "/contacts" and method == "GET":
            self.list_contacts(url.query)
            return
        if url.query:
            raise APIError(422, "validation_error", "Only the contact list accepts query parameters.")
        if path == "/contacts":
            if method != "POST":
                raise APIError(405, "method_not_allowed", "Use GET or POST for this route.")
            fields = validate_contact(self.read_json())
            with store.lock:
                self.check_unique_email(fields["email"])
                contact = {"id": store.next_id, **fields}
                store.contacts[store.next_id] = contact
                store.next_id += 1
                self.respond(201, {"data": contact}, {"Location": f"/contacts/{contact['id']}"})
            return
        if method not in {"GET", "PATCH", "DELETE"}:
            raise APIError(405, "method_not_allowed", "Use GET, PATCH, or DELETE for this route.")
        contact_id = int(match.group(1))
        fields = validate_contact(self.read_json(), partial=True) if method == "PATCH" else None
        with store.lock:
            if contact_id not in store.contacts:
                raise APIError(404, "not_found", "Contact not found.")
            if method == "GET":
                self.respond(200, {"data": store.contacts[contact_id]})
            elif method == "DELETE":
                del store.contacts[contact_id]
                self.respond(200, {"deleted": contact_id})
            else:
                if "email" in fields:
                    self.check_unique_email(fields["email"], excluding=contact_id)
                store.contacts[contact_id].update(fields)
                self.respond(200, {"data": store.contacts[contact_id]})

    def check_unique_email(self, email, excluding=None):
        for contact in self.server.store.contacts.values():
            if contact["id"] != excluding and contact["email"] == email:
                raise APIError(409, "duplicate_email", "A contact with this email already exists.")

    def list_contacts(self, query):
        params = parse_qs(query, keep_blank_values=True)
        if set(params) - {"status", "limit", "offset"} or any(len(values) != 1 for values in params.values()):
            raise APIError(422, "validation_error", "Use supported parameters once each.")
        status = params.get("status", [None])[0]
        if status is not None and status not in STATUSES:
            raise APIError(422, "validation_error", "Use a supported status filter.")
        integers = {}
        for name, default, minimum, maximum in [("limit", "20", 1, 50), ("offset", "0", 0, 1_000_000)]:
            raw = params.get(name, [default])[0]
            if not re.fullmatch(r"[0-9]{1,7}", raw) or not minimum <= int(raw) <= maximum:
                raise APIError(422, "validation_error", f"{name} must be between {minimum} and {maximum}.")
            integers[name] = int(raw)
        with self.server.store.lock:
            contacts = [contact for _, contact in sorted(self.server.store.contacts.items())
                        if status is None or contact["status"] == status]
            limit, offset = integers["limit"], integers["offset"]
            self.respond(200, {"data": contacts[offset:offset + limit],
                               "meta": {"total": len(contacts), "limit": limit, "offset": offset}})


def make_server(port=8765):
    server = ThreadingHTTPServer(("127.0.0.1", port), CRMHandler)
    server.store = ContactStore()
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    server = make_server(args.port)
    print(f"Practice API running at http://127.0.0.1:{args.port}", flush=True)
    print("Local synthetic data only. Press Ctrl+C to stop.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
