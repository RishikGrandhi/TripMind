import type { NaturalLanguagePlanResponse } from '../api/types'
import { cityName, label, money, shortDate } from '../lib/format'
import { Card, EmptyDash, SectionHeading } from './ui'

export function ExtractionSummary({ response }: { response: NaturalLanguagePlanResponse }) {
  const { constraints, preferences } = response.extracted_request
  const destinations = [constraints.destination_city_id, ...constraints.additional_destination_city_ids]
  const duration = Math.round((new Date(constraints.end_date).getTime() - new Date(constraints.start_date).getTime()) / 86400000) + 1

  const hard = [
    ['Hotel ceiling', constraints.max_hotel_price_per_night ? `${money(constraints.max_hotel_price_per_night)} / night` : null],
    ['Flight ceiling', constraints.max_flight_price ? money(constraints.max_flight_price) : null],
    ['Daily travel ceiling', constraints.max_travel_duration_minutes ? `${constraints.max_travel_duration_minutes} minutes` : null],
    ['Allowed modes', constraints.allowed_transport_modes.map(label).join(', ')],
    ['Mandatory destinations', destinations.map(cityName).join(' → ')],
  ]
  const soft = [
    ['Activity categories', preferences.preferred_activity_categories.map(label).join(', ') || null],
    ['Transport', preferences.preferred_transport_modes.map(label).join(', ') || null],
    ['Direct flight', preferences.prefer_direct_flights ? 'Preferred' : null],
    ['Airlines', preferences.preferred_airlines.join(', ') || null],
    ['Hotel amenities', preferences.preferred_hotel_amenities.map(label).join(', ') || null],
    ['Pace', preferences.pace ? label(preferences.pace) : null],
  ]

  return (
    <Card>
      <SectionHeading
        eyebrow="01 · Understanding"
        title="Extracted travel request"
        action={<span className="rounded-full bg-violet-50 px-3 py-1 text-xs font-bold text-violet-700">{label(response.extraction.provider_used)}</span>}
      />
      <div className="grid grid-cols-2 gap-x-5 gap-y-4 border-b border-slate-100 pb-6 sm:grid-cols-3">
        <Fact name="Origin" value={cityName(constraints.origin_city_id)} />
        <Fact name="Destinations" value={destinations.map(cityName).join(' → ')} />
        <Fact name="Dates" value={`${shortDate(constraints.start_date)} – ${shortDate(constraints.end_date)}`} />
        <Fact name="Duration" value={`${duration} days`} />
        <Fact name="Travelers" value={String(constraints.travelers)} />
        <Fact name="Total budget" value={money(constraints.total_budget)} />
      </div>

      <div className="mt-6 grid gap-7 lg:grid-cols-2">
        <FactList title="Hard constraints" items={hard} />
        <FactList title="Soft preferences" items={soft} />
      </div>

      <div className="mt-6 grid gap-3 sm:grid-cols-2">
        <NoteBlock title="Assumptions" notes={response.extraction.assumptions} tone="teal" />
        <NoteBlock title="Warnings" notes={response.extraction.warnings} tone="amber" />
      </div>
      <div className="mt-4 flex flex-wrap gap-3 text-xs text-slate-500">
        <span>Provider requested: <b className="text-slate-700">{label(response.extraction.requested_provider)}</b></span>
        <span>•</span>
        <span>Fallback used: <b className="text-slate-700">{response.extraction.fallback_used ? 'Yes' : 'No'}</b></span>
        {response.extraction.fallback_reason && <><span>•</span><span>Reason: {label(response.extraction.fallback_reason)}</span></>}
      </div>
    </Card>
  )
}

function Fact({ name, value }: { name: string; value: string }) {
  return <div><dt className="text-xs font-bold uppercase tracking-wide text-slate-400">{name}</dt><dd className="mt-1 font-semibold text-slate-900">{value}</dd></div>
}

function FactList({ title, items }: { title: string; items: (string | null)[][] }) {
  return <div><h3 className="mb-3 text-sm font-bold text-slate-950">{title}</h3><dl className="space-y-2">{items.map(([name, value]) => <div key={name!} className="flex justify-between gap-4 text-sm"><dt className="text-slate-500">{name}</dt><dd className="text-right font-medium text-slate-800">{value || <EmptyDash />}</dd></div>)}</dl></div>
}

function NoteBlock({ title, notes, tone }: { title: string; notes: string[]; tone: 'teal' | 'amber' }) {
  const colors = tone === 'teal' ? 'border-teal-100 bg-teal-50/70 text-teal-950' : 'border-amber-100 bg-amber-50/70 text-amber-950'
  return <div className={`rounded-2xl border p-4 ${colors}`}><h3 className="text-xs font-extrabold uppercase tracking-wide">{title}</h3>{notes.length ? <ul className="mt-2 space-y-1 text-sm">{notes.map((note) => <li key={note}>• {note}</li>)}</ul> : <p className="mt-2 text-sm opacity-60">None</p>}</div>
}
