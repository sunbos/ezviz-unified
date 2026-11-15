# Whole-JSON-body request contracts — offline evaluation (H014)

Date: 2026-11-15
Scope: `harness/registry.py`, `harness/wire_eval.py`, `tests/test_harness_whole_json_body.py`
No SDK, real registry, real case, or account changes.

## Finding

Several OTAP write endpoints document a single `Body` parameter whose value
is a *bare JSON object* — not a set of named form fields and not a named
JSON field. The reviewed transport-contract model could only express
`location=body` parameters (url-encoded form values or fields merged into a
JSON object), so these APIs could not be described — and therefore not
evaluated — faithfully. Fabricating a `Body` wrapper parameter or
hard-coding product fields (e.g. `timeZone`) would have falsified the
contract.

## Contract extension

`contract.request_body` is optional; absent means behaviour is completely
unchanged. Only this exact descriptor shape is legal:

```json
"request_body": {"type": "object", "required": true, "evidence": "<source note>"}
```

Rules enforced identically by `harness/registry.py::_request_body` (structural
audit, `RegistryError`) and `harness/wire_eval.py::_check_contract`
(`contract_invalid` problem code):

- `type` must be `"object"` and `required` must be `true`; optional or
  non-object bodies are unsupported and are rejected explicitly;
- `evidence` must be a non-empty string; unknown descriptor keys are rejected;
- `body_encoding` must be `"json"`;
- `token_location` must be `none`, `query`, or `header`;
- no `location=body` parameter may coexist — the whole object *is* the body.

## Case extension

`case.json_body` is the standalone complete JSON object:

- required exactly when the contract declares `request_body`, and it must
  decode to a `dict`; forbidden on every other contract (`case_invalid`);
- `fields` continues to carry named `query`/`header` parameters only —
  unknown names and missing required headers are still rejected
  (`unknown_field` / `missing_required_field`);
- rejected inside `json_body` (`case_invalid` unless noted): non-string
  keys, NaN/Infinity, non-JSON values produced by decoding (datetime/DTO
  tags), a top-level `accessToken` key (`managed_token_field`), and any
  `json_fields` use (there are no `body` locations to name);
- keys *inside* `json_body` — including a nested `accessToken` — are product
  data and are never reinterpreted as undeclared top-level HTTP parameters;
- `$ref`/`$format` placeholders decode exactly like `fields` do.

## Wire verification

- Token injection, `Content-Type: application/json`, path/origin/host,
  query, header, and request-count checks are unchanged; for whole-body
  contracts the expected `json` bucket is simply the entire object.
- Comparison reuses `_strict_equal`: recursive and type-strict
  (bool/int/float/str/null are distinct, `1 != true != 1.0`), arrays
  order-sensitive, objects order-insensitive — type-equality illusions
  cannot hide tampering.

## Explicitly unsupported (rejected, never silently overridden)

- `token_location: "json"` combined with `request_body` — the managed token
  cannot share a caller-owned whole body (`contract_invalid`, and
  `_expected_wire` refuses rather than overwriting a key);
- `token_location: "form"` or any `body_encoding` other than `json`;
- `request_body` alongside any `location=body` parameter (named-field
  mixing);
- `required: false` / non-`object` body types / extra descriptor keys /
  non-object or null descriptor values;
- auth operations (`cn.token.get`, `global.token.get`) can never carry
  `json_body`.

## Test strategy

`tests/test_harness_whole_json_body.py` substitutes synthetic
`Client`/`AsyncClient` classes via `monkeypatch`; the evaluator's transport
capture, comparison, dispatch, counting, and classification all run for
real — a tampered body/header/token fails every cell, so passes are earned,
never self-reported. Coverage: full 8-cell grids for header/query/none
tokens on read-only and write operations, 13 wire-tamper variants, 14
case-level rejections, 10 contract-level rejections, direct
`_check_business_request` type-tampering via `httpx.MockTransport`-captured
requests, and registry audit acceptance/rejection. A regression case proves
real contracts still pass unchanged and still forbid `json_body`. All
synthetic contracts/cases live only in the test file — they are not added
to the real registry, real cases, or supported-operation statistics.
