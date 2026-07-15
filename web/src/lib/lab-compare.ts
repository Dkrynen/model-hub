export type LabMetricDirection = "higher" | "lower";
export type LabStanding = "best" | "second" | "tie";
export type LabMetricKey =
  | "tokens_per_second"
  | "pre_generation_ms"
  | "load_duration_ms"
  | "prompt_eval_duration_ms"
  | "total_duration_ms";

export interface LabComparableMeasurement {
  model: string;
  protocol_id?: string | null;
  num_ctx?: number | null;
  tokens_per_second?: number | null;
  pre_generation_ms?: number | null;
  load_duration_ms?: number | null;
  prompt_eval_duration_ms?: number | null;
  total_duration_ms?: number | null;
}

function validMetric(key: LabMetricKey, value: number | null | undefined): value is number {
  if (value == null || !Number.isFinite(value)) return false;
  return key === "tokens_per_second" ? value > 0 : value >= 0;
}

export function measurementsAreComparable(
  rows: readonly LabComparableMeasurement[],
  key: LabMetricKey,
): rows is readonly [LabComparableMeasurement, LabComparableMeasurement] {
  if (rows.length !== 2) return false;
  const [left, right] = rows;
  return Boolean(
    left.protocol_id
      && left.protocol_id === right.protocol_id
      && left.num_ctx != null
      && left.num_ctx === right.num_ctx
      && validMetric(key, left[key])
      && validMetric(key, right[key]),
  );
}

export function measurementsShareProtocol(
  rows: readonly LabComparableMeasurement[],
): rows is readonly [LabComparableMeasurement, LabComparableMeasurement] {
  if (rows.length !== 2) return false;
  const [left, right] = rows;
  return Boolean(
    left.protocol_id
      && left.protocol_id === right.protocol_id
      && left.num_ctx != null
      && left.num_ctx === right.num_ctx,
  );
}

export function metricStandings(
  rows: readonly LabComparableMeasurement[],
  key: LabMetricKey,
  direction: LabMetricDirection,
): Record<string, LabStanding> {
  if (!measurementsAreComparable(rows, key)) return {};
  const [left, right] = rows;
  const leftValue = left[key] as number;
  const rightValue = right[key] as number;
  if (leftValue === rightValue) return { [left.model]: "tie", [right.model]: "tie" };
  const leftWins = direction === "higher" ? leftValue > rightValue : leftValue < rightValue;
  return {
    [left.model]: leftWins ? "best" : "second",
    [right.model]: leftWins ? "second" : "best",
  };
}

export function updateCompareSelection(
  selection: readonly string[],
  model: string,
): { selection: string[]; limitReached: boolean } {
  if (selection.includes(model)) {
    return { selection: selection.filter((item) => item !== model), limitReached: false };
  }
  if (selection.length >= 2) return { selection: [...selection], limitReached: true };
  return { selection: [...selection, model], limitReached: false };
}
