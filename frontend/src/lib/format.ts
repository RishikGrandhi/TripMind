export const cityNames: Record<string, string> = {
  'city-mum': 'Mumbai',
  'city-goi': 'Goa',
  'city-del': 'Delhi',
  'city-jai': 'Jaipur',
  'city-maa': 'Chennai',
  'city-agr': 'Agra',
  'city-sml': 'Shimla',
  'city-hyd': 'Hyderabad',
}

export function cityName(id: string | null): string {
  if (!id) return '—'
  return cityNames[id] ?? id
}

export function money(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === '') return '—'
  const parsed = Number(value)
  return Number.isFinite(parsed)
    ? new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 2 }).format(parsed)
    : String(value)
}

export function label(value: string): string {
  return value.replaceAll('_', ' ').replace(/\b\w/g, (character) => character.toUpperCase())
}

export function readableFact(value: string): string {
  return Object.entries(cityNames).reduce(
    (result, [id, name]) => result.replaceAll(id, name),
    value,
  )
}

export function shortDate(value: string): string {
  return new Intl.DateTimeFormat('en-IN', { day: 'numeric', month: 'short', year: 'numeric' }).format(
    new Date(`${value}T00:00:00`),
  )
}

export function time(value: string): string {
  return new Intl.DateTimeFormat('en-IN', { hour: 'numeric', minute: '2-digit' }).format(new Date(value))
}

export function duration(minutes: number | null): string {
  if (minutes === null) return ''
  const hours = Math.floor(minutes / 60)
  const remainder = minutes % 60
  return [hours ? `${hours}h` : '', remainder ? `${remainder}m` : ''].filter(Boolean).join(' ')
}
