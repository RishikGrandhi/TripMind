import type { AgentTraceRecord, TripState } from '../api/types'
import { label, money } from '../lib/format'
import { Card, SectionHeading } from './ui'

const statusStyle: Record<AgentTraceRecord['status'], string> = {
  succeeded: 'bg-emerald-100 text-emerald-700',
  approved: 'bg-teal-100 text-teal-700',
  rejected: 'bg-rose-100 text-rose-700',
  fallback: 'bg-amber-100 text-amber-800',
  failed: 'bg-rose-100 text-rose-700',
}

export function AgentActivity({ state }: { state: TripState }) {
  if (state.agent_trace.length === 0) return null

  return (
    <Card className="overflow-hidden">
      <SectionHeading eyebrow="03 · Agent activity" title="AI agent / tool trace" />
      <p className="-mt-4 mb-6 max-w-3xl text-sm text-slate-500">
        Completed factual actions and results from the synchronous planning run. This is an execution audit, not hidden reasoning.
      </p>
      <ol className="relative space-y-3 before:absolute before:bottom-5 before:left-[1.05rem] before:top-5 before:w-px before:bg-slate-200">
        {state.agent_trace.map((entry) => <TraceEntry key={`${entry.step}-${entry.action}`} entry={entry} />)}
      </ol>
    </Card>
  )
}

function TraceEntry({ entry }: { entry: AgentTraceRecord }) {
  const change = formatChange(entry)
  return (
    <li className="relative pl-11">
      <span className="absolute left-0 top-3 grid h-9 w-9 place-items-center rounded-full border-4 border-white bg-slate-900 text-xs font-black text-white">{entry.step}</span>
      <div className="rounded-2xl border border-slate-200 bg-white p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <p className="font-bold text-slate-950">{label(entry.action)}</p>
            <p className="mt-1 text-xs text-slate-500">
              {entry.tool ? label(entry.tool) : label(entry.provider)}
              {entry.reason_code ? ` · ${label(entry.reason_code)}` : ''}
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            {entry.source && <Badge>{label(entry.source)}</Badge>}
            <span className={`rounded-full px-2.5 py-1 text-[0.7rem] font-extrabold uppercase tracking-wide ${statusStyle[entry.status]}`}>{entry.status}</span>
          </div>
        </div>
        {entry.result_summary && <p className="mt-3 text-sm leading-relaxed text-slate-600">{entry.result_summary}</p>}
        <div className="mt-3 flex flex-wrap gap-x-5 gap-y-1 text-xs text-slate-500">
          {entry.violation && <span><b className="text-slate-700">Violation:</b> {label(entry.violation)}</span>}
          {change && <span><b className="text-slate-700">Change:</b> {change}</span>}
        </div>
      </div>
    </li>
  )
}

function Badge({ children }: { children: string }) {
  return <span className="rounded-full border border-slate-200 bg-slate-50 px-2.5 py-1 text-[0.7rem] font-bold uppercase tracking-wide text-slate-600">{children}</span>
}

function formatChange(entry: AgentTraceRecord): string | null {
  if (entry.before_component_id || entry.after_component_id) {
    return `${entry.before_component_id ?? '—'} → ${entry.after_component_id ?? 'removed'}`
  }
  if (entry.before_value !== null || entry.after_value !== null) {
    const before = typeof entry.before_value === 'string' ? money(entry.before_value) : String(entry.before_value ?? '—')
    const after = typeof entry.after_value === 'string' ? money(entry.after_value) : String(entry.after_value ?? '—')
    return `${before} → ${after}`
  }
  return null
}
