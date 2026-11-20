"""Offline tests for the synthetic OAuth login helpers."""

import asyncio
from collections.abc import Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit

import httpx
import pytest

from ezviz_unified import DiagnosticRecorder
from ezviz_unified.oauth import AsyncOAuth, OAuth, OAuthGrant

KINDS = ("sync", "async")
AUTH = "https://synth-auth.ezvizlife.com"
API = "https://synth-api.ezvizlife.com"
KEY = "SYNKEY%2F?seg"
AT = "SYNACCESS-X"
RT = "SYNREFRESH-X"
OID = "SYNOPEN-X"
STATE = "SYNSTATE-X"
EXTRA = "SYNEXTRA-X"
RETURN = AUTH + "/oauth/success"
BUNDLE = "SYNBUNDLE+ A"


def make(kind, **overrides):
    """Build a real SDK client with the synthetic key and prebound origins."""
    options = {"auth_origin": AUTH, "api_origin": API}
    options.update(overrides)
    cls = OAuth if kind == "sync" else AsyncOAuth
    return cls(KEY, **options)


def call(kind, client, method, *args, **kwargs):
    """Invoke a public method, awaiting coroutines on the async client."""
    target = getattr(client, method)
    if kind == "async":
        return asyncio.run(target(*args, **kwargs))
    return target(*args, **kwargs)


@pytest.fixture(autouse=True)
def _forbid_http(monkeypatch):
    """Any HTTPX send attempt fails the test: these helpers stay local."""

    def _blocked(self, *args, **kwargs):
        raise AssertionError("offline OAuth tests must not perform HTTP")

    monkeypatch.setattr(httpx.Client, "send", _blocked)
    monkeypatch.setattr(httpx.AsyncClient, "send", _blocked)


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize(
    "client_type,bundle",
    [(11, BUNDLE), (13, BUNDLE), (19, None), (19, BUNDLE)],
)
@pytest.mark.parametrize("response_type", ["token", "refresh_token"])
def test_build_login_url(kind, client_type, bundle, response_type):
    client = make(kind)
    kwargs = {"client_type": client_type}
    if bundle is not None:
        kwargs["bundle_id"] = bundle
    if response_type != "token":  # token exercises the default value
        kwargs["response_type"] = response_type

    url = call(kind, client, "build_login_url", 7, **kwargs)

    parts = urlsplit(url)
    assert parts.scheme == "https"
    assert parts.netloc == "openauth.ezvizlife.com"
    assert parts.path == "/oauth/ddns/SYNKEY%252F%3Fseg"
    assert parts.fragment == ""
    assert "#" not in url

    expected = {
        "areaId": str(7),
        "client_type": str(client_type),
        "response_type": response_type,
    }
    if bundle is not None:
        expected["bundleId"] = BUNDLE
    assert sorted(parse_qsl(parts.query, keep_blank_values=True)) == sorted(expected.items())


@pytest.mark.parametrize("kind", KINDS)
def test_parse_redirect_refresh_token(kind):
    client = make(kind)
    supplied = {
        "access_token": AT,
        "refresh_token": RT,
        "open_id": OID,
        "expires_in": "0007",
        "areaDomain": API,
        "authDomain": AUTH,
        "state": STATE,
        "extension": EXTRA + " %2F+ok",
        "blank": "",
    }
    redirect = RETURN + "#" + urlencode(supplied)

    grant = call(
        kind,
        client,
        "parse_redirect",
        redirect,
        response_type="refresh_token",
        expected_state=STATE,
    )

    assert isinstance(grant, OAuthGrant)
    assert grant.access_token == AT
    assert grant.refresh_token == RT
    assert grant.open_id == OID
    assert grant.expires_in == 7
    assert type(grant.expires_in) is int
    assert grant.area_domain == API
    assert grant.auth_domain == AUTH

    assert isinstance(grant.fields, Mapping)
    assert dict(grant.fields) == dict(supplied)

    with pytest.raises(TypeError):
        grant.fields["access_token"] = "tampered"
    with pytest.raises(AttributeError):
        grant.access_token = "tampered"

    sentinels = (AT, RT, OID, STATE, EXTRA, BUNDLE, KEY, AUTH, API, RETURN)
    for text in (str(grant), repr(grant), str(client), repr(client)):
        for sentinel in sentinels:
            assert sentinel not in text


@pytest.mark.parametrize("kind", KINDS)
def test_parse_redirect_token_minimal(kind):
    client = make(kind, diagnostic_recorder=DiagnosticRecorder())
    redirect = RETURN + "#" + urlencode({"access_token": AT})

    grant = call(kind, client, "parse_redirect", redirect)

    assert isinstance(grant, OAuthGrant)
    assert grant.access_token == AT
    assert grant.refresh_token is None
    assert grant.open_id is None
    assert grant.expires_in is None
    assert grant.area_domain is None
    assert grant.auth_domain is None
    assert dict(grant.fields) == {"access_token": AT}
    assert len(grant.fields) == 1


@pytest.mark.parametrize("kind", KINDS)
def test_parse_redirect_token_expires_in_zero(kind):
    client = make(kind)
    redirect = RETURN + "#" + urlencode({"access_token": AT, "expires_in": "0"})

    grant = call(kind, client, "parse_redirect", redirect)

    assert grant.expires_in == 0
    assert grant.fields["expires_in"] == "0"


INVALID_BUILD = [
    (0, {"client_type": 19}),
    (-1, {"client_type": 19}),
    (True, {"client_type": 19}),
    ("1", {"client_type": 19}),
    (1, {"client_type": 1}),
    (1, {"client_type": 12}),
    (1, {"client_type": "11"}),
    (1, {"client_type": True}),
    (1, {"client_type": 11}),
    (1, {"client_type": 11, "bundle_id": ""}),
    (1, {"client_type": 11, "bundle_id": "   "}),
    (1, {"client_type": 13}),
    (1, {"client_type": 13, "bundle_id": ""}),
    (1, {"client_type": 13, "bundle_id": " \t "}),
    (1, {"client_type": 19, "bundle_id": ""}),
    (1, {"client_type": 19, "bundle_id": "  "}),
    (1, {"client_type": 19, "response_type": "code"}),
    (1, {"client_type": 19, "response_type": 1}),
]


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("area_id,kwargs", INVALID_BUILD)
def test_build_login_url_invalid_inputs(kind, area_id, kwargs):
    client = make(kind)
    with pytest.raises((ValueError, TypeError)):
        call(kind, client, "build_login_url", area_id, **kwargs)


OTHER_AUTH = "https://synth-auth9.ezvizlife.com"
OTHER_API = "https://synth-api9.ezvizlife.com"
AT_PAIR = urlencode({"access_token": AT})
BADKEY = "SYNBADKEY/raw"


def _ctor(kind, app_key=KEY, **options):
    """Instantiate a client with an arbitrary key and option overrides."""
    opts = {"auth_origin": AUTH, "api_origin": API}
    opts.update(options)
    cls = OAuth if kind == "sync" else AsyncOAuth
    return cls(app_key, **opts)


def _error_chain(exc):
    """Yield every exception reachable via __cause__/__context__ (acyclic)."""
    seen = set()
    stack = [exc]
    while stack:
        current = stack.pop()
        if not isinstance(current, BaseException) or id(current) in seen:
            continue
        seen.add(id(current))
        yield current
        stack.append(current.__cause__)
        stack.append(current.__context__)


def _assert_clean(exc, sentinels):
    """No sentinel may surface in str()/repr() of any chained exception."""
    for item in _error_chain(exc):
        for text in (str(item), repr(item)):
            for sentinel in sentinels:
                assert sentinel not in text


INVALID_CTOR = [
    ("", {}, ()),
    (None, {}, ()),
    (b"synkey", {}, ()),
    ("/", {}, ()),
    (BADKEY, {}, ("SYNBADKEY",)),
    (KEY, {"api_origin": AUTH}, (KEY,)),
    (KEY, {"auth_origin": API}, (KEY,)),
    (KEY, {"api_origin": AUTH + "/"}, (KEY,)),
    (KEY, {"auth_origin": None}, (KEY,)),
    (KEY, {"api_origin": None}, (KEY,)),
    (KEY, {"auth_origin": "http://synth-auth.ezvizlife.com"}, (KEY,)),
    (KEY, {"api_origin": "ftp://synth-api.ezvizlife.com"}, (KEY,)),
    (KEY, {"auth_origin": "https://synth-auth.example.com"}, (KEY,)),
    (KEY, {"api_origin": "https://synth-api.ezvizlife.com.evil.example"}, (KEY,)),
]


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("app_key,options,sentinels", INVALID_CTOR)
def test_oauth_ctor_invalid_inputs(kind, app_key, options, sentinels):
    with pytest.raises((ValueError, TypeError)) as excinfo:
        _ctor(kind, app_key, **options)
    _assert_clean(excinfo.value, (KEY, *sentinels))


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("options", [{}, {"auth_origin": AUTH}, {"api_origin": API}])
def test_oauth_ctor_requires_explicit_origins(kind, options):
    cls = OAuth if kind == "sync" else AsyncOAuth
    with pytest.raises(TypeError):
        cls(KEY, **options)


INVALID_PARSE_URL = [
    (None, {}, (KEY,)),
    (b"https://x", {}, (KEY,)),
    (RETURN + "?" + AT_PAIR, {}, (AT,)),
    (RETURN + "?#" + AT_PAIR, {}, (AT,)),
    (RETURN + "?" + AT_PAIR + "#" + AT_PAIR, {}, (AT,)),
    (RETURN, {}, (KEY,)),
    (RETURN + "#" + urlencode({"open_id": OID}), {}, (OID,)),
    (AUTH + "/oauth/failure#" + AT_PAIR, {}, (AT,)),
    (AUTH + "/oauth/successx#" + AT_PAIR, {}, (AT,)),
    (AUTH + "/OAUTH/success#" + AT_PAIR, {}, (AT,)),
    (AUTH + "/oauth/success/#" + AT_PAIR, {}, (AT,)),
    (API + "/oauth/success#" + AT_PAIR, {}, (AT,)),
    (OTHER_AUTH + "/oauth/success#" + AT_PAIR, {}, (AT,)),
    (
        "https://synth-auth.ezvizlife.com.evil.example/oauth/success#" + AT_PAIR,
        {},
        (AT,),
    ),
    ("http://synth-auth.ezvizlife.com/oauth/success#" + AT_PAIR, {}, (AT,)),
]

INVALID_PARSE_FIELDS = [
    (RETURN + "#access_token=", {}, ()),
    (RETURN + "#access_token=%20%20", {}, ()),
    (RETURN + "#refresh_token=" + RT, {"response_type": "refresh_token"}, (RT,)),
    (RETURN + "#access_token=" + AT + "&access_token=" + AT, {}, (AT,)),
    (RETURN + "#" + AT_PAIR + "&SYNDUP=1&SYNDUP=2", {}, (AT,)),
    (RETURN + "#=v&" + AT_PAIR, {}, (AT,)),
    (RETURN + "#" + AT_PAIR + "&access%5Ftoken=" + AT, {}, (AT,)),
    (RETURN + "#" + AT_PAIR + "&SYNSOLO", {}, (AT,)),
    (RETURN + "#access_token=SYNLEAK%ZZ", {}, ("SYNLEAK",)),
    (RETURN + "#access_token=SYNLEAK%2", {}, ("SYNLEAK",)),
    (RETURN + "#access_token=SYNLEAK%", {}, ("SYNLEAK",)),
    (RETURN + "#SYNLEAK%ZZ=x&" + AT_PAIR, {}, ("SYNLEAK",)),
    (RETURN + "#access_token=SYNUTF8MARK%FFx", {}, ("SYNUTF8MARK",)),
    (
        RETURN + "#" + AT_PAIR + "&x=SYNUTF8MARK%C3%28y",
        {},
        (AT, "SYNUTF8MARK"),
    ),
    (RETURN + "#access_token=SYNRAW tok", {}, ("SYNRAW",)),
    (RETURN + "#access_token=" + AT + "&x=a\tb", {}, (AT,)),
    (RETURN + "#" + AT_PAIR + "&x=a\nb", {}, (AT,)),
    (RETURN + "#" + AT_PAIR, {"response_type": "refresh_token"}, (AT,)),
    (
        RETURN + "#access_token=" + AT + "&refresh_token=",
        {"response_type": "refresh_token"},
        (AT,),
    ),
    (
        RETURN + "#access_token=" + AT + "&refresh_token=%20",
        {"response_type": "refresh_token"},
        (AT,),
    ),
    (RETURN + "#access_token=" + AT + "&expires_in=-1", {}, (AT,)),
    (RETURN + "#access_token=" + AT + "&expires_in=%2B7", {}, (AT,)),
    (RETURN + "#access_token=" + AT + "&expires_in=1.5", {}, (AT,)),
    (RETURN + "#access_token=" + AT + "&expires_in=abc", {}, (AT,)),
    (RETURN + "#access_token=" + AT + "&expires_in=1_0", {}, (AT,)),
    (RETURN + "#access_token=" + AT + "&expires_in=true", {}, (AT,)),
    (RETURN + "#access_token=" + AT + "&expires_in=%EF%BC%97", {}, (AT,)),
    (RETURN + "#" + urlencode({"access_token": AT, "authDomain": API}), {}, (AT,)),
    (RETURN + "#" + urlencode({"access_token": AT, "areaDomain": AUTH}), {}, (AT,)),
    (
        RETURN + "#" + urlencode({"access_token": AT, "authDomain": OTHER_AUTH}),
        {},
        (AT,),
    ),
    (
        RETURN + "#" + urlencode({"access_token": AT, "areaDomain": OTHER_API}),
        {},
        (AT,),
    ),
    (
        RETURN
        + "#"
        + urlencode({"access_token": AT, "authDomain": "http://synth-auth.ezvizlife.com"}),
        {},
        (AT,),
    ),
    (
        RETURN + "#" + urlencode({"access_token": AT, "authDomain": API, "areaDomain": AUTH}),
        {},
        (AT,),
    ),
    (
        RETURN + "#" + urlencode({"access_token": AT, "authDomain": API, "areaDomain": API}),
        {},
        (AT,),
    ),
    (RETURN + "#" + AT_PAIR, {"expected_state": STATE}, (AT, STATE)),
    (
        RETURN + "#" + urlencode({"access_token": AT, "state": "SYNWRONG"}),
        {"expected_state": STATE},
        (AT, STATE, "SYNWRONG"),
    ),
    (RETURN + "#" + AT_PAIR, {"response_type": "code"}, (AT,)),
    (RETURN + "#" + AT_PAIR, {"response_type": "TOKEN"}, (AT,)),
    (RETURN + "#" + AT_PAIR, {"response_type": 11}, (AT,)),
]


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("url,kwargs,sentinels", INVALID_PARSE_URL)
def test_parse_redirect_invalid_url(kind, url, kwargs, sentinels):
    client = make(kind)
    with pytest.raises((ValueError, TypeError)) as excinfo:
        call(kind, client, "parse_redirect", url, **kwargs)
    _assert_clean(excinfo.value, sentinels)


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("url,kwargs,sentinels", INVALID_PARSE_FIELDS)
def test_parse_redirect_invalid_fields(kind, url, kwargs, sentinels):
    client = make(kind)
    with pytest.raises((ValueError, TypeError)) as excinfo:
        call(kind, client, "parse_redirect", url, **kwargs)
    _assert_clean(excinfo.value, sentinels)


DIAG_SENTINELS = (
    KEY,
    AT,
    RT,
    OID,
    STATE,
    EXTRA,
    BUNDLE,
    RETURN,
    AUTH,
    API,
    "SYNBADEXT",
)

DIAG_CALLS = [
    (
        "build_login_url",
        "oauth.build_login_url",
        (7,),
        {"client_type": 19},
        (0,),
        {"client_type": 19},
    ),
    (
        "parse_redirect",
        "oauth.parse_redirect",
        (RETURN + "#" + AT_PAIR,),
        {},
        (
            RETURN
            + "#"
            + urlencode(
                {
                    "access_token": AT,
                    "state": EXTRA,
                    "extension": "SYNBADEXT%ZZ",
                }
            ),
        ),
        {"expected_state": STATE},
    ),
]


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("method,entrypoint,good_args,good_kwargs,bad_args,bad_kwargs", DIAG_CALLS)
@pytest.mark.parametrize("succeed", [True, False])
def test_diagnostic_recorder_events(
    kind, method, entrypoint, good_args, good_kwargs, bad_args, bad_kwargs, succeed
):
    recorder = DiagnosticRecorder()
    client = make(kind, diagnostic_recorder=recorder)

    caught = None
    if succeed:
        call(kind, client, method, *good_args, **good_kwargs)
        outcomes = ("started", "succeeded")
    else:
        with pytest.raises((ValueError, TypeError)) as excinfo:
            call(kind, client, method, *bad_args, **bad_kwargs)
        caught = excinfo.value
        outcomes = ("started", "failed")

    events = recorder.report()["events"]
    assert len(events) == 2
    assert [event["outcome"] for event in events] == list(outcomes)
    for event in events:
        assert event["kind"] == "call"
        assert event["mode"] == kind
        assert event["entrypoint"] == entrypoint

    recorded = recorder.to_json()
    if not isinstance(recorded, str):
        recorded = repr(recorded)
    for sentinel in DIAG_SENTINELS:
        assert sentinel not in recorded
        assert sentinel not in repr(events)
    if caught is not None:
        _assert_clean(caught, DIAG_SENTINELS)
