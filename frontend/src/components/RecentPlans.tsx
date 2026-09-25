import type { PlanningSessionSummary } from '../api/types'
import { cityName, money } from '../lib/format'
import { Card, SectionHeading, StatusBadge } from './ui'

export function RecentPlans({
  sessions,
  loading,
  openingId,
  onOpen,
}: {
  sessions: PlanningSessionSummary[]
  loading: boolean
  openingId: string | null
  onOpen: (sessionId: string) => void
}) {
  return (
    <Card className="mt-6">
      <SectionHeading
        eyebrow="Saved locally"
        title="Recent plans"
        action={<span className="rounded-full bg-slate-100 px-3 py-1 text-xs font-bold text-slate-500">SQLite history</span>}
      />
      {loading ? (
        <p className="text-sm text-slate-500">Loading saved plans…</p>
      ) : sessions.length === 0 ? (
        <div className="rounded-2xl border border-dashed border-slate-200 bg-slate-50 p-5 text-sm text-slate-500">
          Completed and infeasible plans will appear here after you run them.
        </div>
      ) : (
        <div className="grid gap-3 md:grid-cols-2">
          {sessions.map((session) => (
            <button
              key={session.id}
              type="button"
              disabled={openingId !== null}
              onClick={() => onOpen(session.id)}
              className="rounded-2xl border border-slate-200 p-4 text-left transition hover:border-teal-300 hover:bg-teal-50/40 disabled:cursor-wait disabled:opacity-60"
            >
              <div className="flex items-start justify-between gap-3">
                <div>
                  <p className="font-bold text-slate-950">
                    {cityName(session.origin_city_id)} → {session.destination_city_ids.map(cityName).join(' → ')}
                  </p>
                  <p className="mt-1 text-xs text-slate-400">{new Date(session.created_at).toLocaleString('en-IN')}</p>
                </div>
                <StatusBadge status={session.status} />
              </div>
              <div className="mt-3 flex items-center justify-between border-t border-slate-100 pt-3 text-sm">
                <span className="text-slate-500">Final cost</span>
                <span className="font-bold text-slate-900">{money(session.final_cost)}</span>
              </div>
              {openingId === session.id && <p className="mt-2 text-xs font-semibold text-teal-700">Opening saved result…</p>}
            </button>
          ))}
        </div>
      )}
    </Card>
  )
}
