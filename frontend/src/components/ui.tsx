import type { PropsWithChildren, ReactNode } from 'react'

export function Card({ children, className = '' }: PropsWithChildren<{ className?: string }>) {
  return <section className={`rounded-3xl border border-slate-200 bg-white p-6 shadow-sm ${className}`}>{children}</section>
}

export function SectionHeading({ eyebrow, title, action }: { eyebrow: string; title: string; action?: ReactNode }) {
  return (
    <div className="mb-5 flex items-start justify-between gap-4">
      <div>
        <p className="text-xs font-bold uppercase tracking-[0.2em] text-teal-700">{eyebrow}</p>
        <h2 className="mt-1 text-xl font-bold tracking-tight text-slate-950">{title}</h2>
      </div>
      {action}
    </div>
  )
}

export function StatusBadge({ status }: { status: string }) {
  const tone = status === 'completed' || status === 'feasible'
    ? 'border-emerald-200 bg-emerald-50 text-emerald-700'
    : status === 'infeasible'
      ? 'border-amber-200 bg-amber-50 text-amber-800'
      : 'border-rose-200 bg-rose-50 text-rose-700'
  return <span className={`rounded-full border px-3 py-1 text-xs font-extrabold uppercase tracking-wider ${tone}`}>{status}</span>
}

export function EmptyDash() {
  return <span className="text-slate-400">Not specified</span>
}
