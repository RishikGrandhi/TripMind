import type { CostBreakdown, PreferenceScore, TripState } from '../api/types'
import { label, money } from '../lib/format'
import { Card, SectionHeading, StatusBadge } from './ui'

export function PlanStatus({ state }: { state: TripState }) {
  const message = state.status === 'completed'
    ? 'TripMind found a plan that satisfies every hard constraint.'
    : state.status === 'infeasible'
      ? 'The planner completed correctly, but the remaining constraints cannot be satisfied with available demo data.'
      : 'Planning could not complete because of a data or system failure.'
  return (
    <div className={`rounded-3xl border p-6 ${state.status === 'completed' ? 'border-emerald-200 bg-emerald-50' : state.status === 'infeasible' ? 'border-amber-200 bg-amber-50' : 'border-rose-200 bg-rose-50'}`}>
      <div className="flex flex-wrap items-center justify-between gap-3"><div><p className="text-xs font-bold uppercase tracking-[0.18em] text-slate-500">Plan status</p><h2 className="mt-1 text-2xl font-black text-slate-950">{label(state.status)}</h2></div><StatusBadge status={state.status} /></div>
      <p className="mt-3 text-sm text-slate-700">{message}</p>
      {state.status === 'infeasible' && <p className="mt-2 text-sm font-semibold text-amber-900">{state.replanning_attempts.length} targeted action{state.replanning_attempts.length === 1 ? '' : 's'} attempted.</p>}
    </div>
  )
}

export function CostSummary({ costs }: { costs: CostBreakdown }) {
  const rows: [string, string][] = [['Flights / inter-city', costs.flights], ['Hotels', costs.hotels], ['Activities', costs.activities], ['Routes / local transport', costs.local_transport], ['Other', costs.other]]
  return <Card><SectionHeading eyebrow="06 · Exact totals" title="Cost breakdown" /><dl className="space-y-3">{rows.map(([name, value]) => <div key={name} className="flex justify-between text-sm"><dt className="text-slate-500">{name}</dt><dd className="font-semibold text-slate-900">{money(value)}</dd></div>)}<div className="flex justify-between border-t border-slate-200 pt-4 text-lg"><dt className="font-black text-slate-950">Total</dt><dd className="font-black text-teal-700">{money(costs.total)}</dd></div></dl><p className="mt-4 text-xs text-slate-400">Backend-calculated exact INR values. The UI does not recalculate planning costs.</p></Card>
}

export function PreferenceScoreCard({ score }: { score: PreferenceScore }) {
  return <Card><SectionHeading eyebrow="07 · Soft ranking" title="Preference score" /><div className="mb-5 flex items-end gap-2"><span className="text-5xl font-black tracking-tight text-violet-700">{score.total}</span><span className="pb-1 font-bold text-slate-400">/ 100</span></div><div className="space-y-3">{Object.entries(score.components).map(([name, value]) => <div key={name}><div className="mb-1 flex justify-between text-xs"><span className="font-semibold text-slate-600">{label(name)}</span><span className="font-bold text-slate-800">{value}</span></div><div className="h-1.5 overflow-hidden rounded-full bg-slate-100"><div className="h-full rounded-full bg-violet-500" style={{ width: `${Math.max(0, Math.min(100, Number(value)))}%` }} /></div></div>)}</div><p className="mt-5 rounded-xl bg-violet-50 p-3 text-xs leading-relaxed text-violet-900">Preference scoring is applied only after all hard constraints are satisfied.</p></Card>
}

export function ExplanationPanel({ explanation }: { explanation: string }) {
  return <Card className="border-teal-200 bg-teal-50/60"><SectionHeading eyebrow="08 · Structured facts" title="Why this plan?" /><p className="text-sm leading-7 text-slate-700">{explanation}</p></Card>
}
