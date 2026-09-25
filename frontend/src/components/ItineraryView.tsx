import type { Itinerary, ItineraryItem } from '../api/types'
import { cityName, duration, money, shortDate, time } from '../lib/format'
import { Card, SectionHeading } from './ui'

const itemIcons: Record<string, string> = { flight: '✈', hotel: '⌂', activity: '◇', route: '→' }

export function ItineraryView({ itinerary, final = true }: { itinerary: Itinerary; final?: boolean }) {
  return (
    <Card>
      <SectionHeading eyebrow={final ? '05 · Final plan' : '05 · Candidate snapshot'} title={final ? 'Day-wise itinerary' : 'Latest candidate itinerary'} />
      <div className="space-y-6">
        {itinerary.days.map((day, index) => (
          <article key={day.date} className="grid gap-4 sm:grid-cols-[7rem_1fr]">
            <div>
              <p className="text-xs font-extrabold uppercase tracking-wider text-teal-700">Day {index + 1}</p>
              <p className="mt-1 font-bold text-slate-950">{shortDate(day.date)}</p>
            </div>
            <div className="space-y-3">
              {day.items.length ? day.items.map((item) => <ItineraryRow key={item.id} item={item} />) : <div className="rounded-xl border border-dashed border-slate-200 p-4 text-sm text-slate-400">No scheduled items — departure or free day.</div>}
            </div>
          </article>
        ))}
      </div>
    </Card>
  )
}

function ItineraryRow({ item }: { item: ItineraryItem }) {
  const route = item.origin_city_id && item.destination_city_id ? `${cityName(item.origin_city_id)} → ${cityName(item.destination_city_id)}` : cityName(item.city_id)
  return (
    <div className="flex gap-4 rounded-2xl border border-slate-100 bg-slate-50/70 p-4">
      <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-white text-lg shadow-sm">{itemIcons[item.item_type]}</span>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap justify-between gap-2"><div><p className="text-xs font-bold uppercase tracking-wide text-slate-400">{item.item_type} · {route}</p><h3 className="mt-1 font-bold text-slate-900">{item.title}</h3></div><p className="font-bold text-slate-900">{money(item.cost)}</p></div>
        <p className="mt-2 text-sm text-slate-500">{time(item.starts_at)} – {time(item.ends_at)}{item.duration_minutes ? ` · ${duration(item.duration_minutes)}` : ''}</p>
        {item.notes && <p className="mt-1 text-xs text-slate-400">{item.notes}</p>}
      </div>
    </div>
  )
}
