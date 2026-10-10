"""Conservative 3-D research transport, NOT an urban turbulence/accuracy model.

Velocity is the discrete gradient of a harmonic potential around voxelized
impermeable obstacles. This gives conservative incompressible face fluxes but
does not reproduce separation, recirculation, an ABL, or turbulent mixing.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy import sparse
from scipy.sparse.linalg import LinearOperator, cg
from scipy.ndimage import label
from shapely import contains_xy
from shapely.geometry import Polygon

from .errors import ScienceError
from .geometry import _number, validate_scenario

MODEL_VERSION = "phase-a-potential-transport-1.0"
MAX_CELLS = 100000
MAX_STEPS = 15000
LIMITATIONS = [
    "Research preview, not a qualified T1 urban dispersion service or professional planning evidence.",
    "Inviscid potential flow omits turbulence, flow separation and street-canyon recirculation; not RANS/LES/OpenFOAM.",
    "Flat ground and extruded voxel obstacles; 8 m horizontal/4 m vertical grid is not metre-scale accuracy.",
    "Prescribed uniform wind and scalar diffusivity; no atmospheric boundary layer, stability, buoyancy or calm episodes.",
    "Passive tracer only; no measured PM/NO2/AQI, chemistry, vegetation, deposition, particle settling or indoor/health outcomes.",
    "Lateral ambient reservoir and closed top/ground; finite transient end-state, not a steady or hourly/daily average.",
    "No independent urban benchmark, field validation, quantified uncertainty or decision adequacy has been established.",
]


def _cancel(check):
    if check is not None and check():
        raise ScienceError("RUN_CANCELLED", "Scientific calculation was cancelled.")


def validate_conditions(conditions: dict) -> dict:
    if not isinstance(conditions, dict):
        raise ScienceError("INPUT_UNIT_UNKNOWN", "Conditions must be an object.")
    bounds = {"wind_speed_m_s": (0.5, 10), "wind_from_deg": (0, 360),
              "diffusivity_m2_s": (0.1, 30), "background_ug_m3": (0, 5000), "duration_s": (10, 600)}
    result = {}
    for name, (low, high) in bounds.items():
        if name not in conditions:
            raise ScienceError("INPUT_UNIT_UNKNOWN", f"{name} is required; no silent scientific default.")
        value = _number(conditions[name], name)
        if not low <= value <= high:
            raise ScienceError("MODEL_OUT_OF_DOMAIN", f"{name} must be within {low}–{high} for this research template.")
        result[name] = value
    result["wind_from_deg"] %= 360
    unsupported = set(conditions) - set(bounds)
    if unsupported:
        raise ScienceError("PROCESS_UNSUPPORTED", f"Unsupported condition fields: {', '.join(sorted(unsupported))}.")
    return result


def wind_vector(speed: float, direction_from_deg: float) -> tuple[float, float]:
    theta = math.radians(direction_from_deg)
    return -speed * math.sin(theta), -speed * math.cos(theta)


@dataclass
class Grid:
    xmin: float
    ymin: float
    xmax: float
    ymax: float
    zmax: float
    nx: int
    ny: int
    nz: int
    dx: float
    dy: float
    dz: float
    fluid: np.ndarray
    x: np.ndarray
    y: np.ndarray
    z: np.ndarray

    @property
    def volume(self):
        return self.dx * self.dy * self.dz

    def locate(self, x, y, z):
        if not (self.xmin <= x < self.xmax and self.ymin <= y < self.ymax and 0 <= z < self.zmax):
            return None
        return min(int(z / self.dz), self.nz - 1), min(int((y - self.ymin) / self.dy), self.ny - 1), min(int((x - self.xmin) / self.dx), self.nx - 1)


def make_grid(scenario: dict, cell_size_m: float | None = None, vertical_cell_size_m: float | None = None) -> Grid:
    """Resolution override is a test/research helper, never a silent runtime coarsening."""
    audit = validate_scenario(scenario)
    domain = scenario.get("analysis_domain")
    if domain is None:
        xmin, ymin, xmax, ymax = audit["extent"]
        domain = {"xmin": math.floor((xmin - 64) / 8) * 8, "ymin": math.floor((ymin - 64) / 8) * 8,
                  "xmax": math.ceil((xmax + 64) / 8) * 8, "ymax": math.ceil((ymax + 64) / 8) * 8,
                  "zmax": 100, "cell_size_m": 8, "vertical_cell_size_m": 4}
    xmin, ymin, xmax, ymax, zmax = (float(domain[k]) for k in ("xmin", "ymin", "xmax", "ymax", "zmax"))
    h = cell_size_m or float(domain.get("cell_size_m", 8))
    hz = vertical_cell_size_m or float(domain.get("vertical_cell_size_m", 4))
    if not (math.isfinite(h) and math.isfinite(hz) and h >= 1 and hz >= 1 and xmax > xmin and ymax > ymin and zmax > 0):
        raise ScienceError("GEOMETRY_INVALID", "Grid dimensions/resolution are invalid.")
    nx, ny, nz = math.ceil((xmax - xmin) / h), math.ceil((ymax - ymin) / h), math.ceil(zmax / hz)
    if nx * ny * nz > MAX_CELLS:
        raise ScienceError("COMPUTE_LIMIT_EXCEEDED", f"Research grid would contain {nx*ny*nz} cells; limit is {MAX_CELLS}. No automatic coarsening.")
    dx, dy, dz = (xmax - xmin) / nx, (ymax - ymin) / ny, zmax / nz
    x, y, z = xmin + (np.arange(nx) + 0.5) * dx, ymin + (np.arange(ny) + 0.5) * dy, (np.arange(nz) + 0.5) * dz
    xx, yy = np.meshgrid(x, y)
    fluid = np.ones((nz, ny, nx), dtype=bool)
    for building in scenario.get("buildings", []):
        mask = contains_xy(Polygon(building["footprint"]), xx, yy)
        if not np.any(mask):
            raise ScienceError("MESH_FAILED", "A building is unresolved on this grid; refine the method rather than omit it.", [{"object_id": building["id"]}])
        below = z < building["height_m"]
        if not np.any(below):
            raise ScienceError("MESH_FAILED", "Building height is unresolved on the vertical grid.", [{"object_id": building["id"]}])
        fluid[below] &= ~mask
    regions, count = label(fluid)
    if count != 1:
        raise ScienceError("MESH_FAILED", "Voxelized fluid contains disconnected regions.")
    if not fluid.any():
        raise ScienceError("MESH_FAILED", "No fluid cells remain.")
    for kind in ("sources", "receptors"):
        for item in scenario.get(kind, []):
            index = (math.floor(item["z"] / dz), math.floor((item["y"] - ymin) / dy), math.floor((item["x"] - xmin) / dx))
            if not (0 <= index[0] < nz and 0 <= index[1] < ny and 0 <= index[2] < nx) or not fluid[index]:
                raise ScienceError("MESH_FAILED", f"{kind[:-1].capitalize()} falls in a solid/outside raster cell; refine/move it explicitly.", [{"object_id": item["id"]}])
    return Grid(xmin, ymin, xmax, ymax, zmax, nx, ny, nz, dx, dy, dz, fluid, x, y, z)


def potential_velocity(grid: Grid, wind: tuple[float, float], cancel_check=None) -> tuple[list[np.ndarray], dict]:
    """Solve div(grad(phi))=0; no flux through solids/ground/top, ambient lateral phi."""
    _cancel(cancel_check)
    fluid = grid.fluid
    index = np.full(fluid.shape, -1, dtype=int)
    index[fluid] = np.arange(int(fluid.sum()))
    n = int(fluid.sum())
    diagonal = np.zeros(n)
    rhs = np.zeros(n)
    rows, cols, entries = [], [], []
    for axis, spacing in enumerate((grid.dz, grid.dy, grid.dx)):
        left, right = [slice(None)] * 3, [slice(None)] * 3
        left[axis], right[axis] = slice(None, -1), slice(1, None)
        left, right = tuple(left), tuple(right)
        connected = fluid[left] & fluid[right]
        a, b = index[left][connected], index[right][connected]
        conductance = 1 / spacing**2
        diagonal[a] += conductance
        diagonal[b] += conductance
        rows.extend([a, b]); cols.extend([b, a]); entries.extend([np.full(len(a), -conductance), np.full(len(a), -conductance)])
    wx, wy = wind
    for axis, spacing, coords, velocity in ((2, grid.dx, grid.y, wx), (1, grid.dy, grid.x, wy)):
        for side in (0, -1):
            sl = [slice(None)] * 3; sl[axis] = side; sl = tuple(sl)
            active = fluid[sl]
            ids = index[sl][active]
            boundary = (grid.xmin if side == 0 else grid.xmax) if axis == 2 else (grid.ymin if side == 0 else grid.ymax)
            phi_line = wx * boundary + wy * coords if axis == 2 else wy * boundary + wx * coords
            target = np.broadcast_to(phi_line, active.shape)[active]
            conductance = 2 / spacing**2
            diagonal[ids] += conductance
            rhs[ids] += conductance * target
    if np.any(diagonal <= 0):
        raise ScienceError("VERIFICATION_FAILED", "Potential-flow matrix has isolated fluid cells.")
    rows.append(np.arange(n)); cols.append(np.arange(n)); entries.append(diagonal)
    matrix = sparse.coo_matrix((np.concatenate(entries), (np.concatenate(rows), np.concatenate(cols))), shape=(n, n)).tocsr()
    inverse_diagonal = LinearOperator((n, n), matvec=lambda v: v / diagonal)
    xx, yy = np.meshgrid(grid.x, grid.y)
    initial = np.broadcast_to(wx * xx + wy * yy, fluid.shape)[fluid].copy()
    iterations = [0]
    def monitor(_):
        iterations[0] += 1
        if iterations[0] % 10 == 0:
            _cancel(cancel_check)
    potential, info = cg(matrix, rhs, x0=initial, M=inverse_diagonal, rtol=1e-11, atol=1e-11, maxiter=3000, callback=monitor)
    if info != 0 or not np.isfinite(potential).all():
        raise ScienceError("VERIFICATION_FAILED", f"Potential-flow solve did not converge (code {info}).")
    phi = np.zeros(fluid.shape); phi[fluid] = potential
    velocities = []
    for axis, spacing in enumerate((grid.dz, grid.dy, grid.dx)):
        dimensions = list(fluid.shape); dimensions[axis] += 1
        v = np.zeros(dimensions)
        left, right, interior = [slice(None)] * 3, [slice(None)] * 3, [slice(None)] * 3
        left[axis], right[axis], interior[axis] = slice(None, -1), slice(1, None), slice(1, -1)
        left, right, interior = tuple(left), tuple(right), tuple(interior)
        v[interior] = np.where(fluid[left] & fluid[right], (phi[right] - phi[left]) / spacing, 0.0)
        if axis in (1, 2):
            for side in (0, -1):
                sl = [slice(None)] * 3; sl[axis] = side; sl = tuple(sl)
                boundary = (grid.xmin if side == 0 else grid.xmax) if axis == 2 else (grid.ymin if side == 0 else grid.ymax)
                target = wx * boundary + wy * grid.y if axis == 2 else wy * boundary + wx * grid.x
                target = np.broadcast_to(target, phi[sl].shape)
                v[sl] = np.where(fluid[sl], 2 * (phi[sl] - target) / spacing if side == 0 else 2 * (target - phi[sl]) / spacing, 0)
        velocities.append(v)
    divergence = sum(np.diff(v, axis=axis) / spacing for axis, (v, spacing) in enumerate(zip(velocities, (grid.dz, grid.dy, grid.dx))))
    maximum = float(np.abs(divergence[fluid]).max())
    relative = maximum / max(math.hypot(*wind) / min(grid.dx, grid.dy, grid.dz), 1e-12)
    if relative > 1e-7:
        raise ScienceError("VERIFICATION_FAILED", f"Discrete continuity failed: relative divergence {relative:g}.")
    return velocities, {"potential_iterations": iterations[0], "max_divergence_s_1": maximum,
                        "relative_divergence": relative, "continuity_passed": True,
                        "max_vertical_velocity_m_s": float(np.abs(velocities[0]).max())}


def source_field(grid: Grid, sources: list[dict]) -> tuple[np.ndarray, dict]:
    q = np.zeros(grid.fluid.shape)
    xx, yy, zz = np.meshgrid(grid.x, grid.y, grid.z, indexing="xy")
    # meshgrid xy emits (y,x,z); native finite-volume layout is (z,y,x).
    xx, yy, zz = (np.moveaxis(a, -1, 0) for a in (xx, yy, zz))
    total = 0.0
    allocation = []
    for source in sources:
        rate = float(source["rate_g_s"]) * 1e-3
        width = max(grid.dx, grid.dy)
        distance2 = ((xx - source["x"])**2 + (yy - source["y"])**2) / width**2 + ((zz - source["z"]) / grid.dz)**2
        weights = np.where(grid.fluid & (distance2 <= 4), np.exp(-0.5 * distance2), 0)
        if weights.sum() <= 0:
            raise ScienceError("MESH_FAILED", "Source has no fluid allocation cells.", [{"object_id": source["id"]}])
        weights /= weights.sum()
        q += rate * weights / grid.volume
        total += rate
        allocation.append({"source_id": source["id"], "rate_kg_s": rate, "allocation_cells": int((weights > 0).sum()),
                           "horizontal_kernel_sigma_m": width, "vertical_kernel_sigma_m": grid.dz})
    assigned = float(q.sum() * grid.volume)
    relative = abs(assigned - total) / max(total, 1e-20)
    if relative > 1e-10:
        raise ScienceError("VERIFICATION_FAILED", "Source allocation did not conserve inventory mass.")
    return q, {"inventory_rate_kg_s": total, "assigned_rate_kg_s": assigned, "assignment_relative_error": relative,
               "source_assignments": allocation, "source_assignment_passed": True}


def _transport_fluxes(grid: Grid, c: np.ndarray, velocities: list[np.ndarray], diffusion: float, background: float) -> list[np.ndarray]:
    result = []
    for axis, (v, spacing) in enumerate(zip(velocities, (grid.dz, grid.dy, grid.dx))):
        flux = np.zeros_like(v)
        left, right, interior = [slice(None)] * 3, [slice(None)] * 3, [slice(None)] * 3
        left[axis], right[axis], interior[axis] = slice(None, -1), slice(1, None), slice(1, -1)
        left, right, interior = tuple(left), tuple(right), tuple(interior)
        opened = grid.fluid[left] & grid.fluid[right]
        vi = v[interior]
        flux[interior] = np.where(opened, vi * np.where(vi >= 0, c[left], c[right]) - diffusion * (c[right] - c[left]) / spacing, 0)
        if axis in (1, 2):
            for side in (0, -1):
                sl = [slice(None)] * 3; sl[axis] = side; sl = tuple(sl)
                vb, cb = v[sl], c[sl]
                if side == 0:
                    fb = vb * np.where(vb >= 0, background, cb) - 2 * diffusion * (cb - background) / spacing
                else:
                    fb = vb * np.where(vb >= 0, cb, background) - 2 * diffusion * (background - cb) / spacing
                flux[sl] = np.where(grid.fluid[sl], fb, 0)
        result.append(flux)
    return result


def stable_timestep(grid: Grid, velocities: list[np.ndarray], diffusion: float) -> float:
    outgoing = np.zeros(grid.fluid.shape)
    for axis, (v, spacing) in enumerate(zip(velocities, (grid.dz, grid.dy, grid.dx))):
        low, high = [slice(None)] * 3, [slice(None)] * 3
        low[axis], high[axis] = slice(None, -1), slice(1, None)
        outgoing += (np.maximum(v[tuple(high)], 0) + np.maximum(-v[tuple(low)], 0)) / spacing
        # Conservative worst-case two interior diffusion faces, or a half-cell
        # ambient reservoir face: 3D/spacing² gives a safe positivity bound.
        outgoing += 3 * diffusion / spacing**2
    return 0.8 / float(outgoing[grid.fluid].max())


def evolve(grid: Grid, velocities: list[np.ndarray], diffusion: float, background: float, duration: float,
           q: np.ndarray, initial: np.ndarray | None = None, cancel_check=None) -> tuple[np.ndarray, dict]:
    """Explicit positive FV update. Public helper for independent analytical tests."""
    c = np.full(grid.fluid.shape, background, dtype=float) if initial is None else np.array(initial, dtype=float, copy=True)
    c[~grid.fluid] = 0
    if c.shape != grid.fluid.shape or q.shape != c.shape or np.any(c < 0) or np.any(q < 0):
        raise ScienceError("VERIFICATION_FAILED", "Initial concentration/source arrays are invalid.")
    dt_max = stable_timestep(grid, velocities, diffusion)
    steps = math.ceil(duration / dt_max)
    if steps > MAX_STEPS:
        raise ScienceError("COMPUTE_LIMIT_EXCEEDED", f"Stable transport requires {steps} steps; research limit is {MAX_STEPS}.")
    dt = duration / steps
    initial_mass = float(c.sum() * grid.volume)
    source_rate = float(q.sum() * grid.volume)
    boundary_net_in = 0.0
    for step in range(steps):
        if step % 10 == 0:
            _cancel(cancel_check)
        fluxes = _transport_fluxes(grid, c, velocities, diffusion, background)
        tendency = q.copy()
        for axis, (flux, spacing) in enumerate(zip(fluxes, (grid.dz, grid.dy, grid.dx))):
            tendency -= np.diff(flux, axis=axis) / spacing
            boundary_net_in += dt * grid.volume / spacing * (float(np.take(flux, 0, axis=axis).sum()) - float(np.take(flux, -1, axis=axis).sum()))
        c += dt * tendency
        c[~grid.fluid] = 0
        if not np.isfinite(c).all() or float(c[grid.fluid].min()) < -1e-18:
            raise ScienceError("VERIFICATION_FAILED", "Transport produced non-finite/negative physical concentration; no clipping applied.")
    _cancel(cancel_check)
    final_mass = float(c.sum() * grid.volume)
    emitted_mass = source_rate * duration
    residual = final_mass - initial_mass - emitted_mass - boundary_net_in
    scale = max(abs(initial_mass), abs(final_mass), abs(emitted_mass), abs(boundary_net_in), 1e-18)
    relative_error = abs(residual) / scale
    if relative_error > 1e-8:
        raise ScienceError("VERIFICATION_FAILED", f"Integrated scalar mass budget failed ({relative_error:g}).")
    return c, {"steps": steps, "dt_s": dt, "duration_s": duration, "initial_mass_kg": initial_mass,
               "final_mass_kg": final_mass, "emitted_mass_kg": emitted_mass, "boundary_net_in_mass_kg": boundary_net_in,
               "mass_balance_residual_kg": residual, "mass_balance_relative_error": relative_error,
               "mass_balance_passed": True, "positivity_passed": True,
               "min_concentration_kg_m3": float(c[grid.fluid].min()), "max_concentration_kg_m3": float(c.max())}


def simulate(scenario: dict, conditions: dict, cancel_check=None) -> dict:
    """Execute actual physics approximation, return serializable research output."""
    _cancel(cancel_check)
    audit = validate_scenario(scenario)
    settings = validate_conditions(conditions)
    grid = make_grid(scenario)
    wind = wind_vector(settings["wind_speed_m_s"], settings["wind_from_deg"])
    velocities, flow_diagnostics = potential_velocity(grid, wind, cancel_check)
    q, source_diagnostics = source_field(grid, scenario.get("sources", []))
    c, budget = evolve(grid, velocities, settings["diffusivity_m2_s"], settings["background_ug_m3"] * 1e-9,
                       settings["duration_s"], q, cancel_check=cancel_check)
    metrics = []
    for receptor in scenario.get("receptors", []):
        point = grid.locate(receptor["x"], receptor["y"], receptor["z"])
        valid = point is not None and bool(grid.fluid[point])
        metrics.append({"receptor_id": receptor["id"], "name": receptor["name"],
                        "value_ug_m3": float(c[point] * 1e9) if valid else None,
                        "valid": valid, "reason": None if valid else "Outside valid native fluid cells",
                        "protected": receptor.get("protected", False), "requested_height_m": receptor["z"],
                        "sampling": "piecewise_constant_containing_3d_cell",
                        "cell_centre": [float(grid.x[point[2]]), float(grid.y[point[1]]), float(grid.z[point[0]])] if valid else None,
                        "metric_definition": "transient_end_state_concentration; not averaged exposure/AQI"})
    slice_c = c[0] * 1e9
    slice_mask = grid.fluid[0]
    values = [float(value) if valid else None for value, valid in zip(slice_c.ravel(), slice_mask.ravel())]
    return {"field": {"schema_version": "fields-v1", "extent": [grid.xmin, grid.ymin, grid.xmax, grid.ymax],
                      "nx": grid.nx, "ny": grid.ny, "height_m": float(grid.z[0]), "values": values,
                      "mask": slice_mask.ravel().tolist(), "units": "ug/m3", "layout": "row_major_y_then_x",
                      "support": "native_cell_centres; piecewise_constant", "time_support": "end_state_at_duration_s"},
            "metrics": metrics, "diagnostics": {"model_version": MODEL_VERSION, "numerical_verification_passed": True,
                                                "verification_scope": "per_run_continuity_source_mass_positivity_transient_budget; independent urban validation absent",
                                                "geometry": audit, "grid": {"nx": grid.nx, "ny": grid.ny, "nz": grid.nz,
                                                                           "dx_m": grid.dx, "dy_m": grid.dy, "dz_m": grid.dz,
                                                                           "fluid_cells": int(grid.fluid.sum()), "solid_cells": int((~grid.fluid).sum()),
                                                                           "bounds_3d": [grid.xmin, grid.ymin, 0, grid.xmax, grid.ymax, grid.zmax]},
                                                "flow": flow_diagnostics, "sources": source_diagnostics, "transport": budget,
                                                "conditions": settings, "observational_validation": "not_performed",
                                                "independent_benchmark_gate": "open", "decision_adequacy": "not_established"},
            "evidence_kind": "simulated", "qualification": "research_preview", "limitations": LIMITATIONS.copy()}
