"""Checked local-metre solids and wind-aligned preparation geometry.

This checks emitted surfaces, not a snappyHexMesh fluid mesh.
"""
from __future__ import annotations

import math
from collections import Counter

import shapely
from pyproj import CRS
from shapely.geometry import Point, Polygon, box

from science.errors import ScienceError
from .config import finite_number


def fail(code, message, object_id=None):
    raise ScienceError(code, message, [{"object_id": object_id, "message": message}])


def number(value, name):
    try:
        return finite_number(value)
    except (ValueError, TypeError, OverflowError):
        fail("INPUT_UNIT_UNKNOWN", f"{name} must be an explicit finite JSON number.")


def wind_frame(direction):
    theta = math.radians(direction)
    es = [-math.sin(theta), -math.cos(theta)]
    # Snap only cardinal roundoff, preserving a right-handed basis.
    es = [0.0 if abs(v) < 1e-14 else v for v in es]
    et = [-es[1], es[0]]
    return {"input_axes": "east_north_up_metres", "solver_axes": "downwind_crosswind_up_metres",
            "wind_from_deg": direction, "origin_local_m": [0.0, 0.0, 0.0],
            "forward_matrix": [[*es, 0.0], [*et, 0.0], [0.0, 0.0, 1.0]],
            "inverse_matrix": [[es[0], et[0], 0.0], [es[1], et[1], 0.0], [0.0, 0.0, 1.0]],
            "determinant": 1.0, "meteorological_convention": "clockwise_from_true_north"}


def rotate(x, y, transform):
    m = transform["forward_matrix"]
    return [m[0][0]*x + m[0][1]*y, m[1][0]*x + m[1][1]*y]


def check_frame(scenario):
    origin, frame = scenario.get("origin"), scenario.get("coordinate_frame")
    if not isinstance(origin, dict) or not isinstance(frame, dict):
        fail("CRS_UNRESOLVED", "Attach the audited local AEQD metre coordinate frame; no guessed coordinates.")
    lon, lat = number(origin.get("longitude"), "origin.longitude"), number(origin.get("latitude"), "origin.latitude")
    if not (-180 <= lon <= 180 and -80 <= lat <= 80):
        fail("CRS_UNRESOLVED", "Origin exceeds the supported WGS84 small-site bounds.")
    required = {"kind": "local_azimuthal_equidistant", "axis_order": "x_east_y_north", "unit": "m",
                "source_crs": "EPSG:4326", "source_axis_order": "longitude_latitude",
                "vertical_reference": "flat_ground_z0; heights_metres_AGL"}
    if any(frame.get(k) != v for k, v in required.items()) or number(frame.get("north_rotation_deg"), "north rotation") != 0:
        fail("CRS_UNRESOLVED", "Only the attached east/north AEQD metres and flat-ground AGL frame is supported.")
    if not isinstance(frame.get("origin"), dict) or any(number(frame["origin"].get(k), "frame origin") != v for k, v in (("longitude", lon), ("latitude", lat))):
        fail("CRS_UNRESOLVED", "Coordinate-frame origin differs from scenario origin.")
    try:
        recorded = CRS.from_wkt(frame["horizontal_crs_wkt"])
        expected = CRS.from_proj4(f"+proj=aeqd +lat_0={lat:.12f} +lon_0={lon:.12f} +datum=WGS84 +units=m +no_defs")
    except Exception:
        fail("CRS_UNRESOLVED", "Coordinate-frame WKT cannot be verified.")
    if not recorded.equals(expected):
        fail("CRS_UNRESOLVED", "Coordinate-frame WKT is not the declared metre AEQD projection.")


def _cross(a, b, c):
    return (b[0]-a[0])*(c[1]-a[1]) - (b[1]-a[1])*(c[0]-a[0])


def surface(points, height, object_id):
    """Constrained caps plus outward walls; check manifold edges and volume."""
    if not hasattr(shapely, "constrained_delaunay_triangles"):
        fail("DEPENDENCY_UNAVAILABLE", "Constrained triangulation requires Shapely >=2.1 and GEOS >=3.10.")
    polygon = Polygon(points)
    if not polygon.exterior.is_ccw:
        points = list(reversed(points))
        polygon = Polygon(points)
    vertices = [[x, y, 0.0] for x, y in points] + [[x, y, height] for x, y in points]
    indices = {tuple(p): i for i, p in enumerate(points)}
    n, faces, caps = len(points), [], []
    try:
        triangles = list(shapely.constrained_delaunay_triangles(polygon).geoms)
    except Exception:
        fail("GEOMETRY_INVALID", "Constrained footprint triangulation failed.", object_id)
    for triangle in triangles:
        coords = list(triangle.exterior.coords)[:-1]
        if len(coords) != 3 or not polygon.covers(triangle):
            fail("GEOMETRY_INVALID", "A cap triangle leaves the physical footprint.", object_id)
        if _cross(*coords) < 0:
            coords.reverse()
        try:
            a, b, c = [indices[tuple(p)] for p in coords]
        except KeyError:
            fail("GEOMETRY_INVALID", "Unexpected triangulation vertex; no inferred geometry.", object_id)
        faces.extend([[c, b, a], [a+n, b+n, c+n]])
        caps.append(triangle)
    if not caps or polygon.symmetric_difference(shapely.union_all(caps)).area > 1e-8 * polygon.area:
        fail("GEOMETRY_INVALID", "Constrained cap triangulation has a gap or overlap.", object_id)
    if abs(sum(t.area for t in caps)-polygon.area) > 1e-8*polygon.area:
        fail("GEOMETRY_INVALID", "Cap triangle areas do not conserve footprint area.", object_id)
    for i in range(n):
        j = (i+1) % n
        faces.extend([[i, j, j+n], [i, j+n, i+n]])
    directed, undirected, volume = Counter(), Counter(), 0.0
    for face in faces:
        a, b, c = [vertices[i] for i in face]
        normal = [(b[1]-a[1])*(c[2]-a[2])-(b[2]-a[2])*(c[1]-a[1]),
                  (b[2]-a[2])*(c[0]-a[0])-(b[0]-a[0])*(c[2]-a[2]),
                  (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])]
        if sum(v*v for v in normal) <= 1e-20:
            fail("GEOMETRY_INVALID", "Degenerate surface triangle.", object_id)
        volume += sum(a[i]*normal[i] for i in range(3))/6
        for i, j in zip(face, face[1:]+face[:1]):
            directed[(i, j)] += 1
            undirected[tuple(sorted((i, j)))] += 1
    if any(v != 2 for v in undirected.values()) or any(directed[(j, i)] != v for (i, j), v in directed.items()):
        fail("GEOMETRY_INVALID", "Surface is not a consistently oriented closed two-manifold.", object_id)
    target = polygon.area*height
    if volume <= 0 or not math.isclose(volume, target, rel_tol=1e-8, abs_tol=1e-7):
        fail("GEOMETRY_INVALID", "Signed extrusion volume does not equal footprint area times height.", object_id)
    return {"vertices": vertices, "faces": faces, "area_m2": polygon.area, "volume_m3": volume,
            "watertight": True, "manifold_edge_count": len(undirected), "cap_triangles": len(caps)}


def prepare_geometry(scenario, config):
    if not isinstance(scenario, dict):
        fail("GEOMETRY_INVALID", "Scenario must be a JSON object.")
    check_frame(scenario)
    transform = wind_frame(config.wind_from_deg)
    solids, sources, receptors, bounds, local_bounds, ids = [], [], [], [], [], set()
    collections = [scenario.get(k) for k in ("buildings", "sources", "receptors")]
    if any(not isinstance(items, list) for items in collections) or any(len(items) > limit for items, limit in zip(collections, [150, 200, 200])):
        fail("IMPORT_LIMIT_EXCEEDED", "Explicit building/source/receptor arrays and bounded counts are required.")
    def identity(item, allowed):
        if not isinstance(item, dict) or set(item)-set(allowed):
            fail("PROCESS_UNSUPPORTED", "Object has unsupported physical fields; no silently omitted process.")
        object_id = item.get("id")
        if not isinstance(object_id, str) or not 1 <= len(object_id) <= 100 or object_id in ids:
            fail("GEOMETRY_INVALID", "Object IDs must be unique bounded strings.")
        ids.add(object_id)
        return object_id
    total_vertices = 0
    for index, building in enumerate(collections[0]):
        object_id = identity(building, ["id", "name", "footprint", "height_m"])
        footprint = building.get("footprint")
        if not isinstance(footprint, list) or not 3 <= len(footprint) <= 1000:
            fail("GEOMETRY_INVALID", "Footprint requires 3–1000 metre vertices, without repeated closure.", object_id)
        points = []
        for point in footprint:
            if not isinstance(point, (list, tuple)) or len(point) != 2:
                fail("GEOMETRY_INVALID", "Footprint vertices must be exact [east,north] metre pairs.", object_id)
            points.append([number(point[0], "east"), number(point[1], "north")])
        total_vertices += len(points)
        if total_vertices > 10000 or any(abs(v) > 1500 for p in points for v in p):
            fail("IMPORT_LIMIT_EXCEEDED", "Geometry exceeds the supported vertex or local-metre bounds.")
        if len(set(map(tuple, points))) != len(points):
            fail("GEOMETRY_INVALID", "Repeated footprint vertices are not admitted.", object_id)
        for i, p in enumerate(points):
            a, b = points[i-1], points[(i+1) % len(points)]
            if math.dist(p, b) < 1e-6 or abs(_cross(a, p, b)) < 1e-10:
                fail("GEOMETRY_INVALID", "Zero edges or collinear/sliver vertices require an explicit geometry correction.", object_id)
        polygon = Polygon(points)
        if not polygon.is_valid or polygon.area < 4:
            fail("GEOMETRY_INVALID", "Footprint must be simple with area at least 4 m².", object_id)
        height = number(building.get("height_m"), "height_m")
        if not 1 <= height <= 80:
            fail("MODEL_OUT_OF_DOMAIN", "Building height must be 1–80 m in this preparation envelope.", object_id)
        rotated = [rotate(*p, transform) for p in points]
        local_bounds.extend(points)
        polygon = Polygon(rotated)
        if any(polygon.distance(s["polygon"]) <= 1e-6 for s in solids):
            fail("GEOMETRY_INVALID", "Building solids overlap or touch; no coincident/non-manifold solids.", object_id)
        mesh = surface(rotated, height, object_id)
        solids.append({"id": object_id, "patch": f"building_{index:04d}", "height_m": height,
                       "polygon": polygon, "mesh": mesh})
        bounds.extend(rotated)
    half = config.source_box_half_width_m
    for kind, items in zip(("source", "receptor"), collections[1:]):
        for index, item in enumerate(items):
            allowed = ["id", "name", "x", "y", "z", "rate_g_s"] if kind == "source" else ["id", "name", "x", "y", "z", "protected"]
            object_id = identity(item, allowed)
            x, y, z = [number(item.get(k), k) for k in ("x", "y", "z")]
            if abs(x) > 1500 or abs(y) > 1500 or not 0 <= z <= 100:
                fail("MODEL_OUT_OF_DOMAIN", "Source/receptor exceeds local metre or AGL bounds.", object_id)
            xy = rotate(x, y, transform)
            local_bounds.append([x, y])
            if any(s["polygon"].covers(Point(xy)) and z <= s["height_m"] for s in solids):
                fail("SOURCE_IN_SOLID" if kind == "source" else "RECEPTOR_IN_SOLID", "Point lies in/on a building.", object_id)
            if kind == "source":
                rate = number(item.get("rate_g_s"), "rate_g_s")
                if not 0 <= rate <= 100:
                    fail("SOURCE_RATE_MISSING", "Source rate must be 0–100 g/s.", object_id)
                lower, upper = [xy[0]-half, xy[1]-half, max(0.0, z-half)], [xy[0]+half, xy[1]+half, z+half]
                footprint = box(*lower[:2], *upper[:2])
                if any(lower[2] <= s["height_m"] and footprint.intersects(s["polygon"]) for s in solids):
                    fail("SOURCE_SUPPORT_IN_SOLID", "Finite emission support box crosses/touches a building; move or explicitly reduce support.", object_id)
                sources.append({"id": object_id, "zone": f"source_{index:04d}", "position_solver_m": [*xy, z],
                                "rate_g_s": rate, "rate_kg_s": rate*1e-3, "support_box_solver_m": {"min": lower, "max": upper},
                                "geometric_support_volume_m3": math.prod(b-a for a, b in zip(lower, upper)),
                                "ground_truncated": z < half, "actual_mesh_cell_count": None,
                                "actual_fluid_mesh_volume_m3": None, "assignment_verified": False})
                bounds.extend([lower[:2], upper[:2]])
            else:
                if z <= 0:
                    fail("RECEPTOR_ON_BOUNDARY", "Receptor must be above flat ground in fluid, not on its wall.", object_id)
                if not isinstance(item.get("protected", False), bool):
                    fail("GEOMETRY_INVALID", "Receptor protected flag must be boolean.", object_id)
                receptors.append({"id": object_id, "position_solver_m": [*xy, z], "position_local_m": [x, y, z],
                                  "protected": item.get("protected", False), "native_mesh_cell": None,
                                  "native_support": "pending_actual_mesh_cell_and_interpolation_audit"})
                bounds.append(xy)
    if not bounds or not receptors:
        fail("GEOMETRY_INVALID", "At least one fluid receptor and spatial object are required for a prepared study.")
    east, north = zip(*local_bounds)
    if max(max(east)-min(east), max(north)-min(north)) > 1500:
        fail("MODEL_OUT_OF_DOMAIN", "Native local study span exceeds 1.5 km; split the study explicitly.")
    xs, ys = zip(*bounds)
    if max(max(xs)-min(xs), max(ys)-min(ys)) > 2200:
        fail("MODEL_OUT_OF_DOMAIN", "Rotated study/support span exceeds the bounded 1.5 km local-site diagonal.")
    return transform, solids, sources, receptors, bounds


def prepare_domain(solids, sources, receptors, bounds, config):
    building_h = max([s["height_m"] for s in solids], default=0.0)
    feature_h = max([building_h, config.reference_height_m, config.base_cell_size_m] +
                    [s["support_box_solver_m"]["max"][2] for s in sources] +
                    [r["position_solver_m"][2] for r in receptors])
    xs, ys = zip(*bounds)
    h = config.base_cell_size_m
    lower = [math.floor((min(xs)-config.upstream_h*feature_h)/h)*h,
             math.floor((min(ys)-config.lateral_h*feature_h)/h)*h, 0.0]
    upper = [math.ceil((max(xs)+config.downstream_h*feature_h)/h)*h,
             math.ceil((max(ys)+config.lateral_h*feature_h)/h)*h,
             math.ceil((feature_h+config.top_h*feature_h)/h)*h]
    counts = [int(round((b-a)/h)) for a, b in zip(lower, upper)]
    base_cells = math.prod(counts)
    upper_cells = base_cells*8**config.surface_refinement_level
    if upper_cells > config.max_cells:
        fail("COMPUTE_LIMIT_EXCEEDED", f"Conservative mesh ceiling {upper_cells} exceeds max_cells={config.max_cells}; choose explicit settings, no automatic coarsening.")
    inside = [lower[0]+h/2, lower[1]+h/2, h/2]
    if any(s["polygon"].covers(Point(inside[:2])) and inside[2] <= s["height_m"] for s in solids):
        fail("GEOMETRY_INVALID", "Selected snappy insidePoint lies in a building.")
    frontal = sum((s["polygon"].bounds[3]-s["polygon"].bounds[1])*s["height_m"] for s in solids)
    blockage_upper = frontal/((upper[1]-lower[1])*upper[2])
    if blockage_upper > 0.05:
        fail("DOMAIN_BLOCKAGE_EXCEEDED", "Conservative projected blockage exceeds the proposed 5% preparation ceiling.")
    return {"min_m": lower, "max_m": upper, "building_max_height_m": building_h, "controlling_height_m": feature_h,
            "clearances_m": {"upstream": min(xs)-lower[0], "downstream": upper[0]-max(xs),
                             "lateral_left": min(ys)-lower[1], "lateral_right": upper[1]-max(ys), "top": upper[2]-feature_h},
            "base_grid_counts": counts, "base_cell_size_m": h, "base_cell_count": base_cells,
            "conservative_refinement_cell_ceiling": upper_cells, "actual_mesh_cell_count": None,
            "snappy_inside_point_m": inside, "projected_blockage_upper_bound": blockage_upper,
            "estimate_kind": "configuration_bound_not_measured_mesh_or_runtime", "actual_connectivity_verified": False}
