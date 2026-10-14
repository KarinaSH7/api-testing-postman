# API testing with Postman

A local CRM API and a collection of requests for practicing API testing. This new educational demo was prepared with AI assistance for Karina to review, run and explain. It is not previous client work.

## Run locally

Python 3.12 was used for verification. The API uses only the standard library.

```sh
python3 app.py
```

The server listens on 127.0.0.1 port 8765. It stores invented contacts in memory and loses changes on restart. Keep it on your own computer. It has no authentication and is not a production service.

## Use Postman

Import both JSON files from the postman folder. Select the Local environment and run the collection in order. The collection contains 39 requests with JavaScript assertions. Reset requests restore the fixture between scenarios. Use the local desktop agent if your Postman version needs it for localhost access.

## Run automated checks

```sh
python3 -m unittest discover -v
```

On 14 October 2026, all 39 Python checks passed. These include 37 HTTP behavior tests and two checks of the collection files. The collection was also executed with Newman. All 39 requests and 117 assertions passed. These are local functional checks, not a load test.

## What to review

The tests cover creating, reading, updating and deleting contacts, pagination, status filters, duplicate emails, malformed JSON and invalid fields. Boundary tests check allowed string lengths and request sizes. Failed updates should leave stored records unchanged. A lock protects duplicate checks during simultaneous creates.

Start with tests/test_api.py and follow each request into app.py. Compare the expected status code, response fields and final stored state. In Postman, read each assertion before running it.

## Practice tasks

- Explain why an invalid input gets 422 and a duplicate email gets 409
- Change a boundary value and predict whether the request succeeds
- Add one new negative test without changing the API
- Explain why a rejected update must not partly change a contact
- Run the collection and record its actual result

## Limitations

This is an in-memory contact API, not a Next.js or Supabase application. There is no database, account system, authorization model, deployment pipeline or load-testing result. Email validation is deliberately simple. The reset route is only appropriate for this disposable local demo.
