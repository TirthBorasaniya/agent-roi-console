// Display formatting only; no values are defined here

export function usd(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return 'n/a'
  const sign = value < 0 ? '-' : ''
  return `${sign}$${Math.abs(value).toFixed(digits)}`
}

export function pct(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined || Number.isNaN(value)) return 'n/a'
  return `${(value * 100).toFixed(digits)}%`
}

export function ratio(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return 'n/a'
  return `${value.toFixed(2)}x`
}

export function seconds(value: number | null | undefined): string {
  if (value === null || value === undefined) return 'n/a'
  return `${value.toFixed(1)} s`
}
