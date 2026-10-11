"""Bounded provider context, actual scoped persistence and fail-closed adverse cases.

Provider response fixtures are deliberately synthetic connector tests, not Delhi
coverage evidence. No test contacts a provider or a live project database.
"""
import asyncio
import base64
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math

import httpx
from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest

from api.explore import (ADAPTER_VERSION, CONTEXT_KIND, MAX_CONTEXTS, PROVIDERS,
                         QUOTA_KIND, QUOTA_TENANT, ExploreBroker, ProviderFailure,
                         _json, normalise_osm, normalise_weather)
from api.explore_schemas import ExploreRequest
from api.main import Identity, create_app, get_store, local_identity
from api.storage import Store, canonical, digest

NOW = datetime(2026, 10, 10, 16, tzinfo=timezone.utc).timestamp()


def request_body(**changes):
    body = {"center": {"longitude": 77.22, "latitude": 28.61},
            "bounds": {"west": 77.217, "south": 28.607, "east": 77.223, "north": 28.613},
            "include_geometry": True, "include_weather": True}
    body.update(changes)
    return body


def osm_fixture():
    ring = [{"lon": 77.219, "lat": 28.609}, {"lon": 77.220, "lat": 28.609},
            {"lon": 77.220, "lat": 28.610}, {"lon": 77.219, "lat": 28.609}]
    return {"osm3s": {"timestamp_osm_base": "2026-10-10T15:58:00Z"}, "elements": [
        {"type": "way", "id": 1, "version": 4, "timestamp": "2024-01-01T00:00:00Z",
         "user": "private-editor", "uid": 987, "tags": {"building": "yes", "height": "12 m", "name": "Test footprint", "contact:phone": "PRIVATE_PHONE"}, "geometry": ring},
        {"type": "way", "id": 2, "tags": {"building": "yes", "building:levels": "3"},
         "geometry": [{"lon": p["lon"] + .001, "lat": p["lat"]} for p in ring]},
        {"type": "way", "id": 3, "tags": {"highway": "residential", "lanes": "2"},
         "geometry": [{"lon": 77.217, "lat": 28.61}, {"lon": 77.223, "lat": 28.61}]},
        {"type": "relation", "id": 4, "tags": {"building": "yes", "type": "multipolygon"}},
    ]}


def weather_fixture():
    return {"latitude": 28.625, "longitude": 77.25, "elevation": 217.0,
            "timezone": "GMT", "utc_offset_seconds": 0,
            "current_units": {"temperature_2m": "°C", "wind_speed_10m": "m/s", "wind_direction_10m": "°"},
            "current": {"time": "2026-10-10T16:00", "interval": 900,
                        "temperature_2m": 29.2, "wind_speed_10m": 3.4, "wind_direction_10m": 270}}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("UA_MODE", "local")
    store, requests = Store(tmp_path), []
    identity = {"tenant": "explore-user-a"}
    def handler(request):
        requests.append(request)
        data = osm_fixture() if request.url.host == "overpass-api.de" else weather_fixture()
        return httpx.Response(200, json=data)
    app = create_app()
    app.state.store = store
    app.state.explore_broker = ExploreBroker(transport=httpx.MockTransport(handler), clock=lambda: NOW)
    app.dependency_overrides[local_identity] = lambda: Identity(identity["tenant"], "test-planner")
    app.dependency_overrides[get_store] = lambda: store
    with TestClient(app) as client:
        yield client, store, app, identity, requests


def saved_context(client, **changes):
    response = client.post("/api/explore/region-context", json=request_body(**changes))
    assert response.status_code == 201, response.text
    return response.json()


def test_catalogue_is_truthful_and_does_not_fetch(setup):
    client, _, _, _, requests = setup
    response = client.get("/api/explore/providers")
    assert response.status_code == 200
    catalogue = response.json()
    assert catalogue["max_area_km2"] == 1 and catalogue["max_side_m"] == 2000
    by_id = {p["id"]: p for p in catalogue["providers"]}
    assert by_id["osm_overpass"]["status"] == "available"
    assert by_id["open_meteo"]["status"] == "available"
    for name in ("openaq", "ogd_cpcb", "google_air", "google_tiles", "open_buildings", "era5_cams"):
        assert by_id[name]["status"] == "not_configured"
        assert not by_id[name]["rights"]["display"]
    for p in by_id.values():
        assert p["rights"]["solver_input"] is False
        assert p["rights"]["export_data"] is False
        assert p["rights"]["commercial_use"] is False
    assert not requests


def test_real_snapshot_persistence_normalization_and_rights(setup):
    client, store, _, identity, requests = setup
    context = saved_context(client)
    assert len(requests) == 2
    assert all(r.headers["user-agent"].startswith("UrbanAirStudio/") for r in requests)
    assert context["scientific_readiness"]["status"] == "blocked"
    assert context["scientific_readiness"]["qualified"] is False
    geometry, weather = context["geometry"], context["weather"]
    assert geometry["building_count"] == 2 and geometry["road_count"] == 1
    assert geometry["missing_height_count"] == 1 and geometry["excluded_relation_count"] == 1
    props = geometry["features"][1]["properties"]
    assert props["height_m"] is None and props["height_basis"] == "missing"
    assert props["tags"]["building:levels"] == "3"  # Never silently turn levels into a height.
    assert weather["model"] == "gfs_global" and weather["wind_speed_10m_m_s"] == 3.4
    assert weather["model_initialization_utc"] is None and weather["lead_time_seconds"] is None
    assert weather["requested_center"] != weather["returned_grid_center"]
    assert weather["qualification"] == "context_only"
    assert context["content_hash"] == digest({k: v for k, v in context.items() if k != "content_hash"})
    assert "PRIVATE_PHONE" not in canonical(context) and "private-editor" not in canonical(context)
    record = store.get(CONTEXT_KIND, context["id"], identity["tenant"])
    raw = base64.b64decode(record["raw_records"][0]["raw_base64"])
    assert "PRIVATE_PHONE" in raw.decode()  # Exact original retained privately, not in API response.
    assert context["snapshots"][0]["raw_sha256"] == "sha256:" + hashlib.sha256(raw).hexdigest()
    assert client.get(f"/api/explore/contexts/{context['id']}").json() == context
    assert not store.list("scenario", identity["tenant"])
    assert not store.list("run", identity["tenant"])


def test_selection_only_contacts_requested_provider(setup):
    client, _, _, _, requests = setup
    context = saved_context(client, include_geometry=False)
    assert len(requests) == 1 and requests[0].url.host == "api.open-meteo.com"
    assert context["geometry"] is None and len(context["snapshots"]) == 1
    assert requests[0].url.params["models"] == "gfs_global"
    assert requests[0].url.params["wind_speed_unit"] == "ms"
    assert "apikey" not in requests[0].url.params


def test_project_and_context_tenant_isolation_before_network(setup):
    client, _, _, identity, requests = setup
    project = client.post("/api/projects", json={"name": "Independent test site"}).json()
    context = saved_context(client, project_id=project["id"])
    count = len(requests)
    identity["tenant"] = "explore-user-b"
    assert client.get(f"/api/explore/contexts/{context['id']}").status_code == 404
    denied = client.post("/api/explore/region-context", json=request_body(project_id=project["id"]))
    assert denied.status_code == 404 and len(requests) == count
    assert client.get("/api/explore/contexts/context_bad").status_code == 404


@pytest.mark.parametrize("path, method, body", [
    ("/api/explore/providers", "get", None),
    ("/api/explore/region-context", "post", request_body()),
    ("/api/explore/contexts/context_" + "a" * 32, "get", None),
])
def test_local_auth_never_accepts_remote_peer(tmp_path, monkeypatch, path, method, body):
    monkeypatch.setenv("UA_MODE", "local")
    client = TestClient(create_app(tmp_path), base_url="http://127.0.0.1", client=("192.0.2.1", 50000))
    response = getattr(client, method)(path, **({"json": body} if body else {}))
    assert response.status_code == 403 and response.json()["detail"]["code"] == "LOCAL_IDENTITY_ONLY"


def test_nonlocal_mode_live_provider_closed(setup, monkeypatch):
    client, _, _, _, requests = setup
    monkeypatch.setenv("UA_MODE", "cognito")
    assert all(p["status"] == "not_configured" for p in client.get("/api/explore/providers").json()["providers"])
    response = client.post("/api/explore/region-context", json=request_body())
    assert response.status_code == 409 and not requests


@pytest.mark.parametrize("changes", [
    {"center": {"longitude": "77.22", "latitude": 28.61}},
    {"center": {"longitude": True, "latitude": 28.61}},
    {"center": {"longitude": 77.22, "latitude": 89}},
    {"center": {"longitude": 1, "latitude": 1}},
    {"bounds": {"west": 177, "east": -177, "south": 28, "north": 29}},
    {"bounds": {"west": 77, "east": 78, "south": 28, "north": 29}},
    {"bounds": {"west": 77.21999, "east": 77.22001, "south": 28.60999, "north": 28.61001}},
    {"include_geometry": "true"},
    {"include_geometry": False, "include_weather": False},
    {"url": "http://169.254.169.254/latest/meta-data/"},
    {"query": "[out:json];node;out;"},
    {"api_key": "secret"},
])
def test_invalid_or_unbounded_request_denied_without_fetch(setup, changes):
    client, _, _, _, requests = setup
    response = client.post("/api/explore/region-context", json=request_body(**changes))
    assert response.status_code == 422 and not requests


def test_one_km_navigation_extent_roundoff_is_accepted():
    latitude, longitude = 28.61, 77.22
    half_x = .5 / (111.32 * math.cos(math.radians(latitude)))
    half_y = .5 / 111.32
    body = request_body(bounds={"west": longitude - half_x, "east": longitude + half_x,
                                "south": latitude - half_y, "north": latitude + half_y})
    assert ExploreRequest.model_validate(body)
    body["bounds"]["east"] += .0001
    with pytest.raises(ValidationError):
        ExploreRequest.model_validate(body)


def test_nonfinite_json_rejected_without_echo(setup):
    client, _, _, _, requests = setup
    value = canonical(request_body()).replace('77.22', 'NaN', 1)
    response = client.post("/api/explore/region-context", content=value, headers={"content-type": "application/json"})
    assert response.status_code == 422 and not requests
    assert '"input"' not in response.text


@pytest.mark.parametrize("payload", [b'{"elements": [], "elements": []}', b'{"elements": NaN}', b'[]', b'{', b'\xff'])
def test_provider_json_fail_closed(payload):
    with pytest.raises(ProviderFailure):
        _json(payload)


def test_cache_is_exact_response_not_shared_mutable_object(setup):
    client, store, app, identity, requests = setup
    first = saved_context(client)
    second = saved_context(client)
    assert len(requests) == 2 and first["id"] != second["id"]
    assert all(s["cached"] for s in second["snapshots"])
    assert first["snapshots"][0]["raw_sha256"] == second["snapshots"][0]["raw_sha256"]
    assert first["snapshots"][0]["id"] != second["snapshots"][0]["id"]
    record = store.get(CONTEXT_KIND, first["id"], identity["tenant"])
    record["context"]["geometry"]["features"].clear()
    with store.transaction() as db:
        db.execute("UPDATE objects SET data=? WHERE kind=? AND id=? AND tenant_id=?", (canonical(record), CONTEXT_KIND, first["id"], identity["tenant"]))
    assert client.get(f"/api/explore/contexts/{first['id']}").status_code == 503
    assert len(client.get(f"/api/explore/contexts/{second['id']}").json()["geometry"]["features"]) == 3
    assert len(next(iter(app.state.explore_broker.cache.values()))["result"]["data"]["features"]) == 3


def test_global_quota_survives_restart_and_tenant_changes(setup):
    client, _, app, identity, requests = setup
    saved_context(client)
    transport = app.state.explore_broker.transport
    app.state.explore_broker = ExploreBroker(transport=transport, clock=lambda: NOW)
    identity["tenant"] = "explore-user-b"
    context = saved_context(client)
    assert len(requests) == 2  # Reservation is global in the store, not per browser/process.
    assert all(s["status"] == "rate_limited" for s in context["snapshots"])
    assert context["geometry"] is None and context["weather"] is None


def test_daily_quota_cannot_be_multiplied_by_new_regions(setup):
    client, store, _, _, requests = setup
    state = {"id": "global", "providers": {"osm_overpass": {"day": "2026-10-10", "count": 20, "next_at": 0}}}
    with store.transaction() as db:
        Store.put(db, QUOTA_KIND, QUOTA_TENANT, state)
    context = saved_context(client, include_weather=False)
    assert context["snapshots"][0]["status"] == "rate_limited" and not requests


def test_context_allowance_denied_before_fetch(setup):
    client, store, _, identity, requests = setup
    with store.transaction() as db:
        for i in range(MAX_CONTEXTS):
            Store.put(db, CONTEXT_KIND, identity["tenant"], {"id": f"limit-{i}"})
    response = client.post("/api/explore/region-context", json=request_body())
    assert response.status_code == 409 and not requests


class Chunks(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield b"x" * 60_000
        yield b"y" * 60_000


@pytest.mark.parametrize("kind, expected", [
    ("429", "rate_limited"), ("redirect", "provider_error"), ("html", "invalid_response"),
    ("length", "invalid_response"), ("streamed", "invalid_response"),
    ("malformed", "invalid_response"), ("compressed", "invalid_response"), ("timeout", "timeout"), ("connection", "provider_error"),
])
def test_adverse_http_never_retries_or_redirects(setup, kind, expected):
    client, _, app, _, requests = setup
    def handler(request):
        requests.append(request)
        if kind == "429":
            return httpx.Response(429, headers={"retry-after": "120"})
        if kind == "redirect":
            return httpx.Response(302, headers={"location": "http://127.0.0.1/private"})
        if kind == "timeout":
            raise httpx.ReadTimeout("PRIVATE_PROVIDER_ERROR", request=request)
        if kind == "connection":
            raise httpx.ConnectError("PRIVATE_PROVIDER_ERROR", request=request)
        if kind == "html":
            return httpx.Response(200, text="<html>PRIVATE_PROVIDER_ERROR</html>")
        if kind == "compressed":
            # Streaming avoids eager decoding in the mock itself, allowing the
            # application to reject the encoding before reading any body chunk.
            return httpx.Response(200, headers={"content-type": "application/json", "content-encoding": "gzip"}, stream=Chunks())
        if kind == "length":
            return httpx.Response(200, headers={"content-type": "application/json", "content-length": "100001"}, content=b'{}')
        if kind == "streamed":
            return httpx.Response(200, headers={"content-type": "application/json"}, stream=Chunks())
        return httpx.Response(200, headers={"content-type": "application/json"}, content=b'{"private":"PRIVATE_PROVIDER_ERROR",')
    app.state.explore_broker.transport = httpx.MockTransport(handler)
    context = saved_context(client, include_geometry=False)
    assert len(requests) == 1 and context["snapshots"][0]["status"] == expected
    assert "PRIVATE_PROVIDER_ERROR" not in canonical(context)
    assert context["weather"] is None and context["scientific_readiness"]["qualified"] is False
    if kind == "429":
        saved_context(client, include_geometry=False)
        assert len(requests) == 1


def test_empty_osm_is_no_coverage_not_assumed_city(setup):
    client, _, app, _, _ = setup
    app.state.explore_broker.transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"elements": []}))
    context = saved_context(client, include_weather=False)
    assert context["snapshots"][0]["status"] == "no_coverage"
    assert context["geometry"]["features"] == []


@pytest.mark.parametrize("mutation", ["remark", "wrong_elements", "objects", "metadata", "timestamp", "topology", "coordinates"])
def test_osm_changed_schema_and_invalid_topology_quarantined(mutation):
    data = osm_fixture()
    if mutation == "remark":
        data["remark"] = "runtime timeout"
    elif mutation == "wrong_elements":
        data["elements"] = {}
    elif mutation == "objects":
        data["elements"] = data["elements"] * 251
    elif mutation == "metadata":
        data["osm3s"] = "unexpected"
    elif mutation == "timestamp":
        data["osm3s"]["timestamp_osm_base"] = "not-a-time"
    else:
        data["elements"] = [data["elements"][0]]
        if mutation == "topology":
            data["elements"][0]["geometry"] = [{"lon": 77.22, "lat": 28.61}] * 4
        else:
            data["elements"][0]["geometry"][0]["lon"] = float("inf")
    with pytest.raises(ProviderFailure):
        normalise_osm(data, ExploreRequest.model_validate(request_body()).bounds)


def test_osm_duplicate_id_missing_height_and_invalid_object_are_retained_as_qc():
    data = osm_fixture()
    data["elements"].append(deepcopy(data["elements"][0]))
    data["elements"][0]["tags"]["height"] = "36 ft"
    data["elements"].append({"type": "way", "id": 5, "tags": {}, "geometry": []})
    geometry, warnings, missing, _ = normalise_osm(data, ExploreRequest.model_validate(request_body()).bounds)
    assert geometry["invalid_object_count"] == 2 and geometry["missing_height_count"] == 2
    assert len(geometry["features"]) == 3 and warnings and missing


@pytest.mark.parametrize("mutation", ["unit", "timezone", "future", "time", "coordinate", "bool", "negative_wind"])
def test_weather_schema_units_time_support_never_silently_coerced(mutation):
    data = weather_fixture()
    if mutation == "unit":
        data["current_units"]["wind_speed_10m"] = "km/h"
    elif mutation == "timezone":
        data["utc_offset_seconds"] = 19800
    elif mutation == "future":
        data["current"]["time"] = "2026-10-11T16:00"
    elif mutation == "time":
        data["current"]["time"] = "unknown"
    elif mutation == "coordinate":
        data["latitude"] = 50
    elif mutation == "bool":
        data["current"]["wind_speed_10m"] = True
    else:
        data["current"]["wind_speed_10m"] = -2
    with pytest.raises(ProviderFailure):
        normalise_weather(data, ExploreRequest.model_validate(request_body()).center, NOW)


def test_weather_stale_and_missing_values_explicit():
    data = weather_fixture()
    data["current"]["time"] = "2026-10-10T10:00"
    data["current"]["temperature_2m"] = None
    weather, warnings, missing, metadata, status = normalise_weather(data, ExploreRequest.model_validate(request_body()).center, NOW)
    assert weather["temperature_2m_c"] is None and "temperature_2m" in missing
    assert status == "stale" and metadata["age_seconds_at_fetch"] == 21600
    assert warnings and not metadata["observation"]


def test_weather_all_missing_is_no_coverage_and_far_grid_fails():
    data = weather_fixture()
    for variable in ("temperature_2m", "wind_speed_10m", "wind_direction_10m"):
        data["current"][variable] = None
    center = ExploreRequest.model_validate(request_body()).center
    weather, _, missing, _, status = normalise_weather(data, center, NOW)
    assert status == "no_coverage" and weather["wind_speed_10m_m_s"] is None
    assert "wind_speed_10m" in missing
    data["latitude"] = center.latitude + .3
    with pytest.raises(ProviderFailure):
        normalise_weather(data, center, NOW)


def test_provider_absolute_deadline_does_not_publish_partial_data(setup):
    client, store, app, identity, _ = setup
    async def interrupted(provider_id, params):
        raise TimeoutError("private network state")
    app.state.explore_broker._request = interrupted
    context = saved_context(client, include_geometry=False)
    snapshot = context["snapshots"][0]
    assert snapshot["status"] == "timeout" and snapshot["raw_sha256"] is None and snapshot["raw_bytes"] == 0
    assert context["weather"] is None
    assert client.get(f"/api/explore/contexts/{context['id']}").status_code == 200


def test_cache_expiry_revalidates_and_counted_failure_does_not_invent_data(setup):
    client, store, app, _, requests = setup
    saved_context(client, include_geometry=False)
    app.state.explore_broker.clock = lambda: NOW + 601
    def unavailable(request):
        requests.append(request)
        return httpx.Response(503)
    app.state.explore_broker.transport = httpx.MockTransport(unavailable)
    context = saved_context(client, include_geometry=False)
    assert len(requests) == 2 and context["weather"] is None
    assert context["snapshots"][0]["cached"] is False
    assert context["snapshots"][0]["status"] == "provider_error"
    control = store.get(QUOTA_KIND, "global", QUOTA_TENANT)
    assert control["providers"]["open_meteo"]["count"] == 2


def test_raw_hash_dependency_rechecked_even_with_rehashed_record(setup):
    client, store, _, identity, _ = setup
    context = saved_context(client)
    record = store.get(CONTEXT_KIND, context["id"], identity["tenant"])
    record["raw_records"][0]["raw_base64"] = base64.b64encode(b'{}').decode()
    record["record_hash"] = digest({k: v for k, v in record.items() if k != "record_hash"})
    with store.transaction() as db:
        db.execute("UPDATE objects SET data=? WHERE kind=? AND id=? AND tenant_id=?", (canonical(record), CONTEXT_KIND, context["id"], identity["tenant"]))
    assert client.get(f"/api/explore/contexts/{context['id']}").status_code == 503


def test_request_dependency_rechecked_even_with_rehashed_record(setup):
    client, store, _, identity, _ = setup
    context = saved_context(client)
    record = store.get(CONTEXT_KIND, context["id"], identity["tenant"])
    record["raw_records"][0]["parameters"]["data"] += ";unknown query"
    record["record_hash"] = digest({k: v for k, v in record.items() if k != "record_hash"})
    with store.transaction() as db:
        db.execute("UPDATE objects SET data=? WHERE kind=? AND id=? AND tenant_id=?", (canonical(record), CONTEXT_KIND, context["id"], identity["tenant"]))
    assert client.get(f"/api/explore/contexts/{context['id']}").status_code == 503


def test_openapi_contract_registers_actual_router(setup):
    _, _, app, _, _ = setup
    schema = app.openapi()
    assert "/api/explore/region-context" in schema["paths"]
    assert "ExploreContext" in schema["components"]["schemas"]
    assert schema["components"]["schemas"]["ExploreRequest"]["additionalProperties"] is False
