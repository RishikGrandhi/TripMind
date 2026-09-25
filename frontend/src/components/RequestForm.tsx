import type { FormEvent } from 'react'

const examples = [
  {
    title: 'Flagship budget replan',
    route: 'Mumbai → Goa · ₹30,000',
    query: 'I want to travel from Mumbai to Goa for 4 days with a maximum budget of ₹30,000. I prefer beaches, direct flights and a comfortable hotel.',
  },
  {
    title: 'Feasible trip',
    route: 'Mumbai → Goa · ₹50,000',
    query: 'Plan a 4-day trip from Mumbai to Goa with a budget of ₹50,000. I prefer beaches and direct flights.',
  },
  {
    title: 'Impossible constraint',
    route: 'Flight ceiling · ₹4,000',
    query: 'Plan a 4-day trip from Mumbai to Goa with a budget of ₹30,000 and flight price under ₹4,000.',
  },
  {
    title: 'Multi-city',
    route: 'Chennai → Delhi → Jaipur',
    query: 'Plan Chennai to Delhi and Jaipur from 10 February 2027 to 14 February 2027 with a budget of 100000 rupees.',
  },
]

interface Props {
  query: string
  loading: boolean
  onQueryChange: (query: string) => void
  onSubmit: () => void
}

export function RequestForm({ query, loading, onQueryChange, onSubmit }: Props) {
  function submit(event: FormEvent) {
    event.preventDefault()
    if (query.trim() && !loading) onSubmit()
  }

  return (
    <section className="relative overflow-hidden rounded-[2rem] bg-slate-950 p-6 text-white shadow-2xl shadow-slate-300 sm:p-9">
      <div className="pointer-events-none absolute -right-32 -top-32 h-80 w-80 rounded-full bg-teal-400/20 blur-3xl" />
      <div className="relative">
        <div className="mb-7 flex flex-wrap items-center justify-between gap-4">
          <div>
            <p className="text-xs font-bold uppercase tracking-[0.2em] text-teal-300">Natural-language request</p>
            <h2 className="mt-2 text-2xl font-bold">Where should TripMind take you?</h2>
          </div>
          <span className="rounded-full border border-emerald-400/30 bg-emerald-400/10 px-4 py-2 text-xs font-bold text-emerald-300">
            ● DEMO / LOCAL MODE
          </span>
        </div>

        <form onSubmit={submit}>
          <label htmlFor="travel-request" className="sr-only">Describe your travel request</label>
          <textarea
            id="travel-request"
            value={query}
            onChange={(event) => onQueryChange(event.target.value)}
            disabled={loading}
            rows={5}
            placeholder={'Plan a 4-day trip from Mumbai to Goa under ₹30,000.\nI prefer beaches and direct flights.'}
            className="w-full resize-y rounded-2xl border border-slate-700 bg-slate-900/80 p-5 text-lg leading-relaxed text-white outline-none transition placeholder:text-slate-500 focus:border-teal-400 focus:ring-4 focus:ring-teal-400/10 disabled:cursor-wait"
          />
          <div className="mt-4 flex items-center justify-between gap-4">
            <p className="hidden text-sm text-slate-400 sm:block">Dates may be omitted for deterministic demo planning.</p>
            <button
              type="submit"
              disabled={loading || !query.trim()}
              className="ml-auto min-w-40 rounded-xl bg-teal-400 px-6 py-3 text-sm font-extrabold tracking-wide text-slate-950 transition hover:bg-teal-300 focus:outline-none focus:ring-4 focus:ring-teal-300/30 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {loading ? 'PLANNING…' : 'PLAN TRIP  →'}
            </button>
          </div>
        </form>

        <div className="mt-8 border-t border-slate-800 pt-6">
          <p className="mb-3 text-xs font-bold uppercase tracking-[0.18em] text-slate-500">Try a demo scenario</p>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {examples.map((example) => (
              <button
                type="button"
                key={example.title}
                onClick={() => onQueryChange(example.query)}
                disabled={loading}
                className="rounded-xl border border-slate-700 bg-slate-900/60 p-3 text-left transition hover:border-teal-400/60 hover:bg-slate-800 focus:outline-none focus:ring-2 focus:ring-teal-400 disabled:opacity-50"
              >
                <span className="block text-xs font-bold uppercase tracking-wide text-teal-300">{example.title}</span>
                <span className="mt-1 block text-xs text-slate-400">{example.route}</span>
              </button>
            ))}
          </div>
        </div>
      </div>
    </section>
  )
}
