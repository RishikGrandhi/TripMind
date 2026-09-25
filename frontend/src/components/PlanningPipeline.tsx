import type { NaturalLanguagePlanResponse } from '../api/types'
import { label } from '../lib/format'
import { Card, SectionHeading } from './ui'

export function PlanningPipeline({ response }: { response: NaturalLanguagePlanResponse }) {
  const state = response.result
  const finalValid = state.current_validation?.is_valid === true
  const stages = [
    ['Request extracted', true, label(response.extraction.provider_used)],
    ['Candidate built', Boolean(state.initial_itinerary), 'CandidateBuilder'],
    ['Hard constraints checked', state.validation_history.length > 0, `${state.validation_history.length} validation${state.validation_history.length === 1 ? '' : 's'}`],
    ['Replanning evaluated', true, state.replanning_attempts.length ? `${state.replanning_attempts.length} targeted attempt${state.replanning_attempts.length === 1 ? '' : 's'}` : 'Not required'],
    ['Final plan validated', finalValid, finalValid ? 'All hard constraints pass' : `${state.current_validation?.violations.length ?? 0} violation(s) remain`],
    ['Preferences scored', Boolean(state.preference_score), state.preference_score ? `${state.preference_score.total} / 100` : 'Not applied'],
  ] as const

  return (
    <Card className="bg-slate-950 text-white">
      <SectionHeading eyebrow="02 · System trace" title="Hybrid planning pipeline" />
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {stages.map(([name, complete, detail], index) => (
          <div key={name} className="rounded-2xl border border-slate-800 bg-slate-900 p-4">
            <div className="flex items-start gap-3">
              <span className={`mt-0.5 grid h-7 w-7 shrink-0 place-items-center rounded-full text-sm font-black ${complete ? 'bg-teal-400 text-slate-950' : 'bg-amber-400 text-slate-950'}`}>{complete ? '✓' : '!'}</span>
              <div><p className="text-xs font-bold uppercase tracking-widest text-slate-500">Stage {index + 1}</p><p className="mt-1 font-bold">{name}</p><p className="mt-1 text-sm text-slate-400">{detail}</p></div>
            </div>
          </div>
        ))}
      </div>
      <div className="mt-5 grid grid-cols-2 gap-3 border-t border-slate-800 pt-5 text-sm lg:grid-cols-4">
        <Source name="AI extraction" value={response.extraction.provider_used} />
        <Source name="Travel data" value={response.sources.travel_source} />
        <Source name="Validation" value={response.sources.validation} />
        <Source name="Replanning" value={response.sources.replanning} />
      </div>
    </Card>
  )
}

function Source({ name, value }: { name: string; value: string }) {
  return <div><p className="text-xs uppercase tracking-wide text-slate-500">{name}</p><p className="mt-1 font-semibold text-slate-200">{label(value)}</p></div>
}
