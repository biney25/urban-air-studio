import { useState } from "react";
import { ArrowRight, Box, MapPin, Wind } from "lucide-react";
import type { Operation, Scenario, Selection } from "../types";
import { center, formatValue, polygonArea } from "../geometry";
import { NumericInput } from "./UI";
export default function ObjectInspector({
  scenario,
  selected,
  disabled,
  onOperation,
}: {
  scenario: Scenario;
  selected: Selection | null;
  disabled: boolean;
  onOperation: (op: Operation) => void;
}) {
  const building =
    selected?.kind === "building"
      ? scenario.buildings.find((b) => b.id === selected.id)
      : undefined;
  const source =
    selected?.kind === "source"
      ? scenario.sources.find((s) => s.id === selected.id)
      : undefined;
  const receptor =
    selected?.kind === "receptor"
      ? scenario.receptors.find((r) => r.id === selected.id)
      : undefined;
  const object = building ?? source ?? receptor;
  const [height, setHeight] = useState(building?.height_m ?? 0),
    [rotation, setRotation] = useState(0),
    [dx, setDx] = useState(0),
    [dy, setDy] = useState(0),
    [rate, setRate] = useState(source?.rate_g_s ?? 0),
    [x, setX] = useState(source?.x ?? receptor?.x ?? 0),
    [y, setY] = useState(source?.y ?? receptor?.y ?? 0),
    [z, setZ] = useState(source?.z ?? receptor?.z ?? 2);
  if (!selected || !object)
    return (
      <p className="inspector-message">
        Select a building, source or receptor to inspect its inputs.
      </p>
    );
  return (
    <form
      className="object-inspector"
      onSubmit={(e) => {
        e.preventDefault();
        if (building)
          onOperation({
            type: "building_update",
            object_id: building.id,
            changes: { height_m: height, rotation_deg: rotation, dx, dy },
          });
        else if (source)
          onOperation({
            type: "source_update",
            object_id: source.id,
            changes: { rate_g_s: rate, x, y, z },
          });
        else if (receptor)
          onOperation({
            type: "receptor_update",
            object_id: receptor.id,
            changes: { x, y, z },
          });
      }}
    >
      <div className="object-heading">
        <span className="object-kind">
          {building ? (
            <Box size={18} />
          ) : source ? (
            <Wind size={18} />
          ) : (
            <MapPin size={18} />
          )}
        </span>
        <div>
          <h2>{object.name}</h2>
          <span>
            {selected.kind} · {object.id}
          </span>
        </div>
      </div>
      {building ? (
        <>
          <div className="object-summary">
            <div>
              <span>Footprint</span>
              <strong>
                {formatValue(polygonArea(building.footprint), 0)} m²
              </strong>
            </div>
            <div>
              <span>Centre / ENU</span>
              <strong>
                {center(building)
                  .map((v) => formatValue(v, 1))
                  .join(", ")}{" "}
                m
              </strong>
            </div>
          </div>
          <NumericInput
            id="building-height"
            label="Height above local ground"
            value={height}
            min={1}
            max={80}
            step="any"
            unit="m"
            disabled={disabled}
            onChange={setHeight}
          />
          <NumericInput
            id="building-rotation"
            label="Rotation from current version"
            value={rotation}
            min={-180}
            max={180}
            step="any"
            unit="°"
            disabled={disabled}
            onChange={setRotation}
          />
          <div className="form-grid compact-grid">
            <NumericInput
              id="building-dx"
              label="Move east"
              value={dx}
              min={-500}
              max={500}
              step="any"
              unit="m"
              disabled={disabled}
              onChange={setDx}
            />
            <NumericInput
              id="building-dy"
              label="Move north"
              value={dy}
              min={-500}
              max={500}
              step="any"
              unit="m"
              disabled={disabled}
              onChange={setDy}
            />
          </div>
          <p className="micro">
            Height changes massing, not a verified floor-area programme. No
            planning-code compliance is claimed.
          </p>
        </>
      ) : (
        <>
          <p className="micro">
            Declared local ENU position. The server rejects points inside solids
            or outside the supported domain.
          </p>
          {source ? (
            <NumericInput
              id="source-rate"
              label="Declared source mass rate"
              value={rate}
              min={0}
              max={100}
              step="any"
              unit="g/s"
              disabled={disabled}
              onChange={setRate}
            />
          ) : null}
          <div className="form-grid compact-grid">
            <NumericInput
              id="point-x"
              label="East"
              value={x}
              min={-1000}
              max={1000}
              step="any"
              unit="m"
              disabled={disabled}
              onChange={setX}
            />
            <NumericInput
              id="point-y"
              label="North"
              value={y}
              min={-1000}
              max={1000}
              step="any"
              unit="m"
              disabled={disabled}
              onChange={setY}
            />
          </div>
          <NumericInput
            id="point-z"
            label="Height"
            value={z}
            min={0}
            max={100}
            step="any"
            unit="m"
            disabled={disabled}
            onChange={setZ}
          />
          {receptor ? (
            <p className="micro">
              {receptor.protected
                ? "Protected comparison receptor."
                : "Receptor not designated protected."}{" "}
              Moving it changes support and prevents a direct paired comparison
              with the old position.
            </p>
          ) : null}
        </>
      )}
      <button
        data-testid="apply-edit"
        type="submit"
        className="button primary"
        disabled={disabled}
      >
        Apply as new version
        <ArrowRight size={15} />
      </button>
      <p className="micro">
        Every accepted edit creates an immutable snapshot. Previous versions and
        their results are retained.
      </p>
    </form>
  );
}
