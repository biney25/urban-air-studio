"""Fixed, bounded local evaluation connectors and immutable scoped map context.

No query/URL/credential supplied by a browser reaches a provider. Public APIs
are deliberately not a production India backend, and their context cannot enter
the solver, dataset export or training path through this router.
"""
import asyncio
import base64
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
import os
import re
import threading
import time

import httpx
from fastapi import APIRouter, Depends, Request
from shapely.geometry import LineString, Polygon, box

from api.explore_schemas import (ExploreCatalogue, ExploreContext, ExploreGeometry,
                                 ExploreRequest, ExploreSnapshot, ExploreWeather)
from api.storage import Store, canonical, digest, new_id, utc_now

PROVIDERS = {
    "osm_overpass": {
        "id": "osm_overpass", "name": "OpenStreetMap / Overpass", "category": "geometry",
        "status": "available", "source_url": "https://overpass-api.de/api/interpreter",
        "licence": "ODbL 1.0", "attribution": "© OpenStreetMap contributors",
        "rights": {"display": True, "retain_raw": True, "retain_derived": True,
                   "use_scope": "Manual, bounded local data qualification only; ODbL obligations apply"},
        "limits": {"minimum_interval_seconds": 30, "daily_requests": 20, "cache_seconds": 3600,
                   "maximum_response_bytes": 1_500_000, "maximum_objects": 1000},
        "warnings": ["Public Overpass is not a production application backend. Use licensed regional extracts or a dedicated instance for scale.",
                     "Coverage and heights are incomplete, community supplied and unverified; roads do not supply traffic or emissions.",
                     "Multipolygon relations remain excluded pending a separate topology audit."],
    },
    "open_meteo": {
        "id": "open_meteo", "name": "Open-Meteo / NOAA GFS", "category": "weather",
        "status": "available", "source_url": "https://api.open-meteo.com/v1/forecast",
        "licence": "CC BY 4.0; free hosted endpoint non-commercial only",
        "attribution": "Weather data by Open-Meteo; NOAA GFS model",
        "rights": {"display": True, "retain_raw": True, "retain_derived": True,
                   "use_scope": "Private non-commercial evaluation; commercial or promotional use requires separate provider qualification"},
        "limits": {"minimum_interval_seconds": 10, "daily_requests": 60, "cache_seconds": 600,
                   "maximum_response_bytes": 100_000},
        "warnings": ["Grid-scale model output is weather context, not a local wind measurement or a qualified CFD boundary.",
                     "This forecast endpoint does not attest model initialization/cycle or forecast lead time; these remain missing.",
                     "Hosted commercial production access must be separately qualified."],
    },
}
for _id, _name, _category, _url, _warning in [
    ("openaq", "OpenAQ observations", "observations", "https://docs.openaq.org/", "API key, instrument/source identities, Delhi station inventory and licence qualification required."),
    ("ogd_cpcb", "CPCB / OGD India", "observations", "https://data.gov.in/", "Current official resource ID, API key, field/unit/averaging QA and source rights are not configured."),
    ("google_tiles", "Google photorealistic tiles", "visual_context", "https://developers.google.com/maps/documentation/tile", "Credentials and visual-use rights required; tiles must never supply solver geometry."),
    ("google_air", "Google air context", "observations", "https://developers.google.com/maps/documentation/air-quality", "Credentials, coarse support and agreement-specific display/retention permissions are not configured."),
    ("open_buildings", "Google Research Open Buildings", "geometry", "https://sites.research.google/open-buildings/", "Actual regional files/coverage, dates/confidence, height QA and selected licence must be qualified."),
    ("era5_cams", "ERA5 / CAMS regional context", "regional_background", "https://ads.atmosphere.copernicus.eu/", "Dataset terms, account, model/reanalysis support and local boundary calibration remain unconfigured."),
    ("copernicus_dem", "Copernicus DEM context", "terrain", "https://dataspace.copernicus.eu/", "Actual raster access, licence and vertical datum must be audited; a surface model is not bare-ground survey terrain."),
]:
    PROVIDERS[_id] = {"id": _id, "name": _name, "category": _category,
                      "status": "not_configured", "source_url": _url,
                      "licence": "Unqualified for this application", "attribution": _name,
                      "rights": {"display": False, "retain_raw": False, "retain_derived": False,
                                 "use_scope": "Blocked until access and intended-use permissions are qualified"},
                      "limits": {}, "warnings": [_warning]}

CONTEXT_KIND = "explore_context"
QUOTA_KIND = "explore_broker_control"
QUOTA_TENANT = "internal:explore-provider-broker"
MAX_CONTEXTS = 100
USER_AGENT = "UrbanAirStudio/0.1 local-evaluation (+https://github.com/biney25/urban-air-studio)"
TAG_ALLOWLIST = {"building", "building:part", "height", "min_height", "building:levels", "roof:height",
                 "highway", "width", "lanes", "surface", "name"}
ADAPTER_VERSION = "explore-local-v1"


class ProviderFailure(Exception):
    def __init__(self, status, message, retry_after=None):
        super().__init__(message)
        self.status, self.message, self.retry_after = status, message, retry_after


def _json(raw):
    def no_constant(_):
        raise ValueError("Non-finite provider JSON")
    try:
        def unique_object(pairs):
            result = {}
            for key, item in pairs:
                if key in result:
                    raise ValueError("Duplicate provider JSON key")
                result[key] = item
            return result
        value = json.loads(raw, parse_constant=no_constant, object_pairs_hook=unique_object)
        if not isinstance(value, dict):
            raise ValueError("Expected one provider object")
        return value
    except (ValueError, RecursionError, UnicodeDecodeError) as exc:
        raise ProviderFailure("invalid_response", "Provider JSON did not satisfy the checked response format") from exc


def _number(value, minimum, maximum):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError("Invalid finite provider number")
    return float(value)


def _timestamp(value):
    if not isinstance(value, str) or len(value) > 40:
        raise ValueError("Missing provider time")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _height(tags):
    raw = tags.get("height")
    if not isinstance(raw, str):
        return None
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(?:m)?\s*", raw)
    if not match:
        return None
    value = float(match.group(1))
    return value if 0 < value <= 300 else None


def normalise_osm(data, bounds):
    elements = data.get("elements")
    if not isinstance(elements, list) or len(elements) > 1000 or data.get("remark"):
        raise ProviderFailure("invalid_response", "OSM response is incomplete, exceeds 1,000 objects or contains a provider error")
    features, seen = [], set()
    buildings = roads = missing = relations = invalid = vertices = 0
    extent = box(bounds.west, bounds.south, bounds.east, bounds.north)
    for element in elements:
        if not isinstance(element, dict):
            invalid += 1
            continue
        if element.get("type") == "relation":
            relations += 1
            continue
        try:
            osm_id = element.get("id")
            if element.get("type") != "way" or isinstance(osm_id, bool) or not isinstance(osm_id, int) or osm_id <= 0 or osm_id in seen:
                raise ValueError("Invalid/duplicate OSM ID")
            seen.add(osm_id)
            source_tags = element.get("tags")
            if not isinstance(source_tags, dict) or len(source_tags) > 100 or not all(isinstance(k, str) and isinstance(v, str) and len(k) <= 128 and len(v) <= 300 for k, v in source_tags.items()):
                raise ValueError("Unexpected OSM tag format")
            tags = {k: v for k, v in source_tags.items() if k in TAG_ALLOWLIST}
            geometry = element.get("geometry")
            if not isinstance(geometry, list) or not 2 <= len(geometry) <= 2000:
                raise ValueError("Invalid OSM coordinate array")
            vertices += len(geometry)
            if vertices > 20_000:
                raise ProviderFailure("invalid_response", "OSM geometry exceeds 20,000 coordinates; select a smaller area")
            coordinates = [[_number(p["lon"], -180, 180), _number(p["lat"], -85, 85)] for p in geometry]
            is_building = bool(tags.get("building")) and tags["building"] != "no"
            if is_building:
                if len(coordinates) < 4 or coordinates[0] != coordinates[-1]:
                    raise ValueError("Unclosed footprint")
                shape = Polygon(coordinates)
                if not shape.is_valid or shape.is_empty or shape.area <= 0:
                    raise ValueError("Invalid footprint topology")
                geo = {"type": "Polygon", "coordinates": [coordinates]}
                h = _height(tags)
                buildings += 1
                missing += int(h is None)
            elif tags.get("highway"):
                shape = LineString(coordinates)
                if not shape.is_valid or shape.is_empty or shape.length <= 0:
                    raise ValueError("Invalid road line")
                geo, h = {"type": "LineString", "coordinates": coordinates}, None
                roads += 1
            else:
                raise ValueError("Unexpected semantic object")
            if not shape.intersects(extent):
                raise ValueError("Provider object does not intersect requested bounds")
            properties = {"osm_id": osm_id, "osm_type": "way", "kind": "building" if is_building else "road",
                          "tags": tags, "height_m": h,
                          "height_basis": "osm_height_tag_unverified" if h is not None else "missing",
                          "source_url": f"https://www.openstreetmap.org/way/{osm_id}",
                          "qualification": "unverified_provider_geometry"}
            if isinstance(element.get("timestamp"), str):
                properties["edited_at"] = _timestamp(element["timestamp"]).isoformat()
            if isinstance(element.get("version"), int) and not isinstance(element["version"], bool):
                properties["osm_version"] = element["version"]
            features.append({"type": "Feature", "id": f"osm:way:{osm_id}", "geometry": geo, "properties": properties})
        except (ValueError, TypeError, KeyError, OverflowError):
            invalid += 1
    # Counts are for included objects only, including failures after shape checks.
    buildings = sum(f["properties"]["kind"] == "building" for f in features)
    roads = len(features) - buildings
    missing = sum(f["properties"]["kind"] == "building" and f["properties"]["height_m"] is None for f in features)
    if elements and not features and invalid:
        raise ProviderFailure("invalid_response", "All returned OSM objects failed geometry/semantic checks")
    result = ExploreGeometry(features=features, building_count=buildings, road_count=roads,
                             missing_height_count=missing, excluded_relation_count=relations,
                             invalid_object_count=invalid).model_dump()
    warnings = []
    if missing:
        warnings.append(f"{missing} building footprints lack usable height tags; no assumed extrusion is supplied.")
    if relations:
        warnings.append(f"{relations} multipolygon relations excluded; their holes/topology have not been audited.")
    if invalid:
        warnings.append(f"{invalid} invalid, duplicate or unsupported objects were quarantined.")
    osm_base = None
    provider_metadata = data.get("osm3s", {})
    if not isinstance(provider_metadata, dict):
        raise ProviderFailure("invalid_response", "OSM response metadata changed format")
    if provider_metadata.get("timestamp_osm_base") is not None:
        try:
            osm_base = _timestamp(provider_metadata["timestamp_osm_base"]).isoformat()
        except (ValueError, TypeError):
            raise ProviderFailure("invalid_response", "OSM source timestamp is invalid")
    metadata = {"adapter_version": ADAPTER_VERSION, "osm_base_timestamp": osm_base,
                "coverage": "Returned objects only; no completeness guarantee", "height_reference": "Community height tag; datum and local accuracy unverified",
                "raw_personal_tags_exposed": False, "counts": {k: result[k] for k in ("building_count", "road_count", "missing_height_count", "excluded_relation_count", "invalid_object_count")}}
    return result, warnings, (["audited_building_heights"] if missing else []), metadata


def normalise_weather(data, center, now):
    try:
        if data.get("error") or data.get("utc_offset_seconds") != 0 or data.get("timezone") not in {"GMT", "UTC"}:
            raise ValueError("Unexpected time reference")
        current, units = data["current"], data["current_units"]
        if not isinstance(current, dict) or not isinstance(units, dict):
            raise ValueError("Missing current model weather")
        values = {}
        missing = ["model_initialization_utc", "forecast_lead_time"]
        for variable, unit, lower, upper in [("temperature_2m", "°C", -100, 70), ("wind_speed_10m", "m/s", 0, 120), ("wind_direction_10m", "°", 0, 360)]:
            key = variable if variable in units else variable + "_gfs_global"
            if units.get(key) != unit:
                raise ValueError("Changed provider units")
            value = current.get(key)
            values[variable] = None if value is None else _number(value, lower, upper)
            if value is None:
                missing.append(variable)
        valid = _timestamp(current["time"])
        age = now - valid.timestamp()
        if age < -3600:
            raise ValueError("Provider time unexpectedly in the future")
        interval = current["interval"]
        if isinstance(interval, bool) or not isinstance(interval, int) or not 1 <= interval <= 3600:
            raise ValueError("Unsupported temporal support")
        weather = ExploreWeather(valid_at_utc=valid.isoformat(), temporal_support_seconds=interval,
                                 spatial_support="NOAA GFS Global grid (documented 0.11°/0.25° model support); provider interpolation/downscaling is not a site measurement",
                                 requested_center=center, returned_grid_center={"longitude": _number(data["longitude"], -180, 180), "latitude": _number(data["latitude"], -85, 85)},
                                 grid_elevation_m=None if data.get("elevation") is None else _number(data["elevation"], -500, 10000),
                                 temperature_2m_c=values["temperature_2m"], wind_speed_10m_m_s=values["wind_speed_10m"], wind_from_10m_deg=values["wind_direction_10m"]).model_dump()
        # A response hundreds of kilometres away cannot be treated as this region.
        lat, lon = weather["returned_grid_center"]["latitude"], weather["returned_grid_center"]["longitude"]
        if abs(lat - center.latitude) > .25 or abs(lon - center.longitude) > .25:
            raise ValueError("Provider grid outside requested regional support")
        warnings = ["Model initialization and lead time are not returned; scientific boundary qualification remains blocked."]
        status = "no_coverage" if all(v is None for v in values.values()) else ("stale" if age > 3 * 3600 else "available")
        if status == "stale":
            warnings.append("Returned current model time is more than three hours old.")
        if any(v is None for v in values.values()):
            warnings.append("Missing model variables remain null; they are not replaced by assumptions.")
        return weather, warnings, missing, {"adapter_version": ADAPTER_VERSION, "model_requested": "gfs_global", "model_cycle_attested": False,
                                           "age_seconds_at_fetch": round(age), "observation": False}, status
    except (KeyError, ValueError, TypeError, OverflowError) as exc:
        raise ProviderFailure("invalid_response", "Weather response failed model/unit/time/spatial checks") from exc


def provider_parameters(provider_id, body):
    if provider_id == "open_meteo":
        return {"latitude": body.center.latitude, "longitude": body.center.longitude,
                "models": "gfs_global", "current": "temperature_2m,wind_speed_10m,wind_direction_10m",
                "timezone": "GMT", "wind_speed_unit": "ms", "temperature_unit": "celsius",
                "forecast_days": 1, "cell_selection": "nearest"}
    b = body.bounds
    bbox = ",".join(format(x, ".7f") for x in (b.south, b.west, b.north, b.east))
    return {"data": f'[out:json][timeout:12][maxsize:16777216];(way["building"]({bbox});relation["building"]({bbox});way["highway"]({bbox}););out meta geom;'}


def _context_count(store, tenant, db=None):
    own = db is None
    db = db or store.connect()
    try:
        return db.execute("SELECT COUNT(*) FROM objects WHERE kind=? AND tenant_id=?", (CONTEXT_KIND, tenant)).fetchone()[0]
    finally:
        if own:
            db.close()


class ExploreBroker:
    """One bounded evaluation cache; persistent Store quota reservation is global.

    The private mutable quota control is separate from immutable provider/context
    evidence. Quotas survive process restarts and are reserved before network IO.
    """
    def __init__(self, transport=None, clock=time.time):
        self.transport, self.clock = transport, clock
        self.cache, self.lock = {}, threading.Lock()

    def reserve(self, store, provider_id):
        now = self.clock()
        day = datetime.fromtimestamp(now, timezone.utc).date().isoformat()
        limits = PROVIDERS[provider_id]["limits"]
        with store.transaction() as db:
            state = store.get(QUOTA_KIND, "global", QUOTA_TENANT, db) or {"id": "global", "providers": {}}
            value = state["providers"].get(provider_id, {"day": day, "count": 0, "next_at": 0})
            if value["day"] != day:
                value.update(day=day, count=0)
            wait = max(0, math.ceil(value["next_at"] - now))
            if wait:
                raise ProviderFailure("rate_limited", "Central provider cooldown is active; reuse the saved context or wait", wait)
            if value["count"] >= limits["daily_requests"]:
                raise ProviderFailure("rate_limited", "Local daily evaluation allowance is exhausted; no automated retry", 3600)
            value.update(count=value["count"] + 1, next_at=now + limits["minimum_interval_seconds"])
            state["providers"][provider_id] = value
            if store.get(QUOTA_KIND, "global", QUOTA_TENANT, db):
                db.execute("UPDATE objects SET data=? WHERE kind=? AND id=? AND tenant_id=?", (canonical(state), QUOTA_KIND, "global", QUOTA_TENANT))
            else:
                Store.put(db, QUOTA_KIND, QUOTA_TENANT, state)

    def cooldown(self, store, provider_id, seconds):
        with store.transaction() as db:
            state = store.get(QUOTA_KIND, "global", QUOTA_TENANT, db)
            if state:
                state["providers"][provider_id]["next_at"] = max(state["providers"][provider_id]["next_at"], self.clock() + seconds)
                db.execute("UPDATE objects SET data=? WHERE kind=? AND id=? AND tenant_id=?", (canonical(state), QUOTA_KIND, "global", QUOTA_TENANT))

    async def _request(self, provider_id, params):
        provider = PROVIDERS[provider_id]
        async with httpx.AsyncClient(transport=self.transport, timeout=httpx.Timeout(16, connect=5), follow_redirects=False,
                                    trust_env=False, headers={"User-Agent": USER_AGENT, "Accept": "application/json", "Accept-Encoding": "identity"}) as client:
            method = "POST" if provider_id == "osm_overpass" else "GET"
            arguments = {"data": params} if method == "POST" else {"params": params}
            async with client.stream(method, provider["source_url"], **arguments) as response:
                if response.status_code == 429:
                    retry = response.headers.get("retry-after", "60")
                    delay = int(retry) if retry.isdigit() and len(retry) <= 5 else 60
                    raise ProviderFailure("rate_limited", "Provider returned 429; no retry was attempted", max(30, min(delay, 3600)))
                if response.status_code != 200:
                    raise ProviderFailure("provider_error", "Provider did not return a successful response; no failover or retry was attempted")
                if "json" not in response.headers.get("content-type", "").lower():
                    raise ProviderFailure("invalid_response", "Provider did not return JSON")
                if response.headers.get("content-encoding", "identity").lower().strip() not in {"identity", ""}:
                    raise ProviderFailure("invalid_response", "Unexpected compressed response is rejected before unbounded decompression")
                limit = provider["limits"]["maximum_response_bytes"]
                length = response.headers.get("content-length")
                if length and (not length.isdigit() or len(length) > 10 or int(length) > limit):
                    raise ProviderFailure("invalid_response", "Provider body exceeds the local response-size allowance")
                chunks, count = [], 0
                async for chunk in response.aiter_bytes():
                    count += len(chunk)
                    if count > limit:
                        raise ProviderFailure("invalid_response", "Provider body exceeds the actual streamed response-size allowance")
                    chunks.append(chunk)
                return b"".join(chunks)

    async def fetch(self, store, provider_id, body):
        provider = PROVIDERS[provider_id]
        params = provider_parameters(provider_id, body)
        request_hash = digest({"adapter_version": ADAPTER_VERSION, "provider": provider_id, "parameters": params})
        with self.lock:
            entry = self.cache.get(request_hash)
            if entry and self.clock() - entry["timestamp"] < provider["limits"]["cache_seconds"]:
                result = deepcopy(entry["result"])
                result["snapshot"].update(id=new_id("snapshot"), cached=True)
                return result
        snapshot = ExploreSnapshot(id=new_id("snapshot"), provider_id=provider_id, fetched_at=None,
                                   source_url=provider["source_url"], licence=provider["licence"], attribution=provider["attribution"],
                                   rights=provider["rights"], status="provider_error", cached=False, raw_sha256=None, raw_bytes=0,
                                   request_hash=request_hash, warnings=provider["warnings"], missing_fields=[], metadata={"adapter_version": ADAPTER_VERSION}).model_dump()
        raw, normalised = b"", None
        try:
            self.reserve(store, provider_id)
            raw = await asyncio.wait_for(self._request(provider_id, params), timeout=18)
            snapshot.update(fetched_at=utc_now(), raw_sha256="sha256:" + hashlib.sha256(raw).hexdigest(), raw_bytes=len(raw))
            data = _json(raw)
            if provider_id == "osm_overpass":
                normalised, warnings, missing, metadata = normalise_osm(data, body.bounds)
                status = "available" if normalised["features"] else "no_coverage"
            else:
                normalised, warnings, missing, metadata, status = normalise_weather(data, body.center, self.clock())
            snapshot.update(status=status, warnings=snapshot["warnings"] + warnings, missing_fields=missing, metadata=metadata)
        except ProviderFailure as exc:
            snapshot.update(status=exc.status, warnings=snapshot["warnings"] + [exc.message], retry_after_seconds=exc.retry_after)
            if exc.status == "rate_limited" and exc.retry_after:
                self.cooldown(store, provider_id, exc.retry_after)
        except (httpx.TimeoutException, TimeoutError):
            snapshot.update(status="timeout", warnings=snapshot["warnings"] + ["Provider deadline elapsed; no automatic retry was attempted."])
        except httpx.RequestError:
            snapshot.update(status="provider_error", warnings=snapshot["warnings"] + ["Provider connection failed; no automatic retry was attempted."])
        result = {"snapshot": snapshot, "data": normalised, "raw_base64": base64.b64encode(raw).decode("ascii"), "parameters": params}
        if snapshot["status"] in {"available", "no_coverage", "stale"}:
            with self.lock:
                self.cache[request_hash] = {"timestamp": self.clock(), "result": deepcopy(result)}
                # Bound in-memory raw/normalised cache, including many different areas.
                while len(self.cache) > 32 or sum(len(canonical(v["result"])) for v in self.cache.values()) > 8_000_000:
                    del self.cache[next(iter(self.cache))]
        return result


def _catalogue():
    catalogue = deepcopy(list(PROVIDERS.values()))
    if os.getenv("UA_MODE", "local") != "local":
        for provider in catalogue:
            provider["status"] = "not_configured"
            provider["warnings"].append("Live public evaluation connectors are enabled only in the loopback local workspace.")
    return ExploreCatalogue(providers=catalogue)


def make_router(*, identity_dependency, store_dependency, required, error):
    router = APIRouter(prefix="/api/explore", tags=["Explore context"])

    @router.get("/providers", response_model=ExploreCatalogue)
    def providers(identity=Depends(identity_dependency)):
        return _catalogue()

    @router.post("/region-context", response_model=ExploreContext, status_code=201)
    async def region_context(body: ExploreRequest, request: Request, identity=Depends(identity_dependency), store=Depends(store_dependency)):
        if os.getenv("UA_MODE", "local") != "local":
            error("EXPLORE_EVALUATION_ONLY", "Public-provider evaluation requires the loopback local workspace; production access is unconfigured", 409)
        if body.project_id:
            required(store, "project", body.project_id, identity)
        if _context_count(store, identity.tenant_id) >= MAX_CONTEXTS:
            error("EXPLORE_CONTEXT_LIMIT", "Local context allowance of 100 saved regions reached; review retention with the operator", 409)
        broker = request.app.state.explore_broker
        selected = [p for p, enabled in [("osm_overpass", body.include_geometry), ("open_meteo", body.include_weather)] if enabled]
        results = await asyncio.gather(*(broker.fetch(store, provider_id, body) for provider_id in selected))
        snapshots, geometry, weather = [], None, None
        raw_records = []
        for provider_id, result in zip(selected, results):
            snapshots.append(result["snapshot"])
            raw_records.append({"id": result["snapshot"]["id"], "raw_base64": result["raw_base64"], "parameters": result["parameters"]})
            if provider_id == "osm_overpass":
                geometry = result["data"]
            else:
                weather = result["data"]
        b = body.bounds
        area = (b.north - b.south) * (b.east - b.west) * 111_320 ** 2 * math.cos(math.radians(body.center.latitude)) / 1_000_000
        blockers = ["Provider context is unqualified and is not yet an immutable solver-ready site baseline.",
                    "Geometry alignment, metric frame/vertical datum, local height accuracy and completeness need an independent audit.",
                    "Actual source activity/emission factors, background observations and receptor support are missing.",
                    "Numerical/physical qualification and independent scientific review remain incomplete."]
        if geometry and geometry["missing_height_count"]:
            blockers.append(f"{geometry['missing_height_count']} building heights are missing; no assumed building heights were invented.")
        if weather:
            blockers.append("Forecast model cycle/lead time and site-scale inlet calibration are not qualified.")
        warnings = ["Selected bounds are a navigation extent; WGS84 degrees are not solver metres.",
                    "Fetched context is retained privately for review; this route has no generic dataset export, publication or training function."]
        context = {"schema_version": "explore-context-v1", "id": new_id("context"), "project_id": body.project_id,
                   "created_at": utc_now(), "region": {"center": body.center.model_dump(), "bounds": b.model_dump(),
                   "crs": "EPSG:4326", "axis_order": "longitude,latitude", "approximate_area_km2": area},
                   "snapshots": snapshots, "geometry": geometry, "weather": weather,
                   "scientific_readiness": {"status": "blocked", "qualified": False, "blockers": blockers}, "warnings": warnings}
        context["content_hash"] = digest(context)
        record = {"id": context["id"], "context": context, "raw_records": raw_records}
        record["record_hash"] = digest(record)
        with store.transaction() as db:
            if _context_count(store, identity.tenant_id, db) >= MAX_CONTEXTS:
                error("EXPLORE_CONTEXT_LIMIT", "Local context allowance reached; no context was committed", 409)
            Store.put(db, CONTEXT_KIND, identity.tenant_id, record, body.project_id)
        return context

    @router.get("/contexts/{context_id}", response_model=ExploreContext)
    def context_read(context_id: str, identity=Depends(identity_dependency), store=Depends(store_dependency)):
        if not re.fullmatch(r"context_[a-f0-9]{32}", context_id):
            error("NOT_FOUND", "Context not found in this tenant", 404)
        record = required(store, CONTEXT_KIND, context_id, identity)
        try:
            checked = {k: v for k, v in record.items() if k != "record_hash"}
            context = record["context"]
            if digest(checked) != record.get("record_hash") or digest({k: v for k, v in context.items() if k != "content_hash"}) != context.get("content_hash"):
                raise ValueError("Saved context failed immutable-byte checks")
            snapshots = {s["id"]: s for s in context["snapshots"]}
            if len(snapshots) != len(context["snapshots"]) or len(record["raw_records"]) != len(snapshots):
                raise ValueError("Raw snapshot identity mismatch")
            for raw in record["raw_records"]:
                content = base64.b64decode(raw["raw_base64"], validate=True)
                snapshot = snapshots[raw["id"]]
                if len(content) != snapshot["raw_bytes"] or (snapshot["raw_sha256"] and "sha256:" + hashlib.sha256(content).hexdigest() != snapshot["raw_sha256"]):
                    raise ValueError("Raw provider snapshot mismatch")
                if digest({"adapter_version": ADAPTER_VERSION, "provider": snapshot["provider_id"], "parameters": raw["parameters"]}) != snapshot["request_hash"]:
                    raise ValueError("Raw provider request dependency mismatch")
            ExploreContext.model_validate(context)
        except (ValueError, KeyError, TypeError, AttributeError):
            error("EXPLORE_INTEGRITY_ERROR", "Saved raw provider snapshot failed checks", 503)
        return context

    return router
