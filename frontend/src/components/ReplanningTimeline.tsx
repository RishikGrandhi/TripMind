import type { ReplanningAttempt, TripState } from '../api/types'
import { label, money } from '../lib/format'
import { Card, SectionHeading } from './ui'

export function ReplanningTimeline({ state }: { state: TripState }) {
  const initial = state.initial_itinerary?.costs.total
  const final = state.current_itinerary?.costs.total
  const savings = initial && final ? Number(initial) - Number(final) : null

  return (
    <Card>
      <SectionHeading eyebrow="04 · Targeted repair" title="Replanning audit trail" />
      {state.replanning_attempts.length === 0 ? (
        <div className="rounded-2xl border border-emerald-200 bg-emerald-50 p-5">
          <p className="font-bold text-emerald-900">Initial itinerary already satisfied all hard constraints.</p>
          <p className="mt-1 text-sm text-emerald-700">No replanning was required.</p>
        </div>
      ) : (
        <>
          <div className="mb-7 grid grid-cols-3 divide-x divide-slate-200 rounded-2xl border border-slate-200 bg-slate-50 py-4 text-center">
            <CostStat title="Initial cost" value={money(initial)} />
            <CostStat title="Final cost" value={money(final)} />
            <CostStat title="Savings" value={money(savings)} accent />
          </div>
          {state.validation_history[0] && !state.validation_history[0].is_valid && (
            <div className="mb-6 rounded-2xl border border-rose-200 bg-rose-50 p-4">
              <p className="text-xs font-extrabold uppercase tracking-wider text-rose-700">Initial validation failed</p>
              <ul className="mt-2 space-y-1 text-sm text-rose-900">
                {state.validation_history[0].violations.map((violation) => <li key={violation.code}>• {violation.message}</li>)}
              </ul>
            </div>
          )}
          <ol className="relative space-y-5 before:absolute before:bottom-5 before:left-[1.05rem] before:top-5 before:w-px before:bg-slate-200">
            {state.replanning_attempts.map((attempt) => <Attempt key={attempt.attempt_number} attempt={attempt} />)}
          </ol>
        </>
      )}
    </Card>
  )
}

function CostStat({ title, value, accent = false }: { title: string; value: string; accent?: boolean }) {
  return <div className="px-2"><p className="text-[0.65rem] font-bold uppercase tracking-wider text-slate-400">{title}</p><p className={`mt-1 text-lg font-black sm:text-2xl ${accent ? 'text-teal-700' : 'text-slate-950'}`}>{value}</p></div>
}

function Attempt({ attempt }: { attempt: ReplanningAttempt }) {
  const action = attempt.actions[0]
  const beforeTotal = action?.parameters.before_total
  const afterTotal = action?.parameters.after_total
  const effect = Number(attempt.cost_effect)
  return (
    <li className="relative pl-11">
      <span className={`absolute left-0 top-0 grid h-9 w-9 place-items-center rounded-full border-4 border-white text-sm font-black text-white ${attempt.succeeded ? 'bg-teal-600' : 'bg-amber-500'}`}>{attempt.attempt_number}</span>
      <div className="rounded-2xl border border-slate-200 p-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div><p className="text-xs font-extrabold uppercase tracking-wider text-teal-700">Replanning attempt {attempt.attempt_number}</p><h3 className="mt-1 text-lg font-bold text-slate-950">{label(action?.action ?? attempt.outcome ?? 'Attempt')}</h3></div>
          <span className={`rounded-full px-3 py-1 text-xs font-bold ${attempt.validation_result?.is_valid ? 'bg-emerald-100 text-emerald-700' : attempt.succeeded ? 'bg-amber-100 text-amber-800' : 'bg-rose-100 text-rose-700'}`}>{attempt.validation_result?.is_valid ? 'FEASIBLE' : attempt.succeeded ? 'IMPROVED' : 'NO REPAIR'}</span>
        </div>
        <div className="mt-4 grid gap-3 text-sm sm:grid-cols-2 lg:grid-cols-4">
          <Detail name="Problem" value={attempt.triggered_by.map(label).join(', ')} />
          <Detail name="Changed" value={attempt.before_component_id === attempt.after_component_id ? attempt.before_component_id ?? 'No component changed' : `${attempt.before_component_id ?? '—'} → ${attempt.after_component_id ?? 'Removed'}`} />
          <Detail name="Before → after" value={beforeTotal && afterTotal ? `${money(String(beforeTotal))} → ${money(String(afterTotal))}` : `${String(attempt.before_value ?? '—')} → ${String(attempt.after_value ?? '—')}`} />
          <Detail name="Effect" value={Number.isFinite(effect) && effect !== 0 ? `${effect < 0 ? 'Saved' : 'Added'} ${money(Math.abs(effect))}` : label(attempt.outcome ?? 'No cost change')} />
        </div>
        <div className="mt-4 flex flex-wrap gap-x-5 gap-y-2 border-t border-slate-100 pt-4 text-xs text-slate-500">
          {attempt.tool_name && <span><b className="text-slate-700">Tool:</b> {attempt.tool_name}</span>}
          {attempt.duration_effect_minutes !== null && <span><b className="text-slate-700">Travel time:</b> {attempt.duration_effect_minutes > 0 ? '+' : ''}{attempt.duration_effect_minutes} minutes</span>}
          <span><b className="text-slate-700">Validation:</b> {attempt.validation_result?.is_valid ? 'all constraints pass' : `${attempt.validation_result?.violations.length ?? 0} violation(s) remain`}</span>
        </div>
      </div>
    </li>
  )
}

function Detail({ name, value }: { name: string; value: string }) {
  return <div><p className="text-xs font-bold uppercase tracking-wide text-slate-400">{name}</p><p className="mt-1 font-semibold text-slate-800">{value}</p></div>
}
