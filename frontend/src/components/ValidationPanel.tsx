import type { ConstraintViolation, ValidationResult } from '../api/types'
import { label, readableFact } from '../lib/format'
import { Card, SectionHeading } from './ui'

export function ValidationPanel({ validation }: { validation: ValidationResult }) {
  return (
    <Card>
      <SectionHeading
        eyebrow="03 · Guardrails"
        title="Hard-constraint validation"
        action={<span className={`rounded-full px-3 py-1 text-xs font-extrabold ${validation.is_valid ? 'bg-emerald-100 text-emerald-700' : 'bg-rose-100 text-rose-700'}`}>{validation.is_valid ? 'ALL CHECKS PASS' : 'VIOLATIONS REMAIN'}</span>}
      />
      <div className="grid gap-3 sm:grid-cols-2">
        {validation.checks.map((check) => (
          <div key={check.constraint} className={`rounded-2xl border p-4 ${check.passed ? 'border-emerald-100 bg-emerald-50/50' : 'border-rose-200 bg-rose-50'}`}>
            <div className="flex items-center gap-3">
              <span className={`grid h-7 w-7 place-items-center rounded-full font-black ${check.passed ? 'bg-emerald-500 text-white' : 'bg-rose-500 text-white'}`}>{check.passed ? '✓' : '×'}</span>
              <div><h3 className="font-bold text-slate-900">{label(check.constraint)}</h3><p className={`text-xs font-bold ${check.passed ? 'text-emerald-700' : 'text-rose-700'}`}>{check.passed ? 'PASSED' : 'FAILED'}</p></div>
            </div>
            <dl className="mt-3 grid grid-cols-[4.5rem_1fr] gap-x-2 gap-y-1 text-xs">
              <dt className="text-slate-400">Expected</dt><dd className="text-slate-700">{readableFact(check.expected)}</dd>
              <dt className="text-slate-400">Actual</dt><dd className="text-slate-700">{readableFact(check.actual)}</dd>
              {check.violation_code && <><dt className="text-slate-400">Code</dt><dd className="font-semibold text-rose-700">{check.violation_code}</dd></>}
            </dl>
          </div>
        ))}
      </div>
      {validation.violations.length > 0 && <Violations violations={validation.violations} />}
    </Card>
  )
}

export function Violations({ violations }: { violations: ConstraintViolation[] }) {
  return (
    <div className="mt-5 rounded-2xl border border-rose-200 bg-rose-50 p-5">
      <h3 className="text-sm font-extrabold uppercase tracking-wide text-rose-800">Remaining violations</h3>
      <div className="mt-3 space-y-3">
        {violations.map((violation, index) => (
          <div key={`${violation.code}-${index}`} className="border-l-2 border-rose-300 pl-3">
            <p className="font-bold text-rose-950">{label(violation.code)}</p>
            <p className="mt-1 text-sm text-rose-900">{violation.message}</p>
            {Object.keys(violation.details).length > 0 && <dl className="mt-2 flex flex-wrap gap-x-5 gap-y-1 text-xs text-rose-800">{Object.entries(violation.details).map(([key, value]) => <div key={key}><dt className="inline opacity-60">{label(key)}: </dt><dd className="inline font-semibold">{String(value)}</dd></div>)}</dl>}
          </div>
        ))}
      </div>
    </div>
  )
}
