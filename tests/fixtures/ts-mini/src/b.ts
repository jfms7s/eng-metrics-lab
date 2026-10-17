export function summarizeB(values: number[]) {
  const total = values.reduce((sum, value) => sum + value, 0);
  const average = total / values.length;
  const spread = values.map((value) => Math.abs(value - average));
  const widest = spread.reduce((max, value) => Math.max(max, value), 0);
  const label = "total=" + total + " average=" + average + " widest=" + widest;
  return { total, average, spread, widest, label };
}
