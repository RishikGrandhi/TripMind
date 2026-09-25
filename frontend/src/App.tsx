import { useCallback, useEffect, useState } from 'react'
import type { ClarificationDetail, NaturalLanguagePlanResponse, PlanningSessionSummary, TripState } from './api/types'
import { fetchPlanningSession, fetchRecentPlans, planNaturalLanguageTrip, TripMindApiError } from './api/tripmind'
import { ExplanationPanel, CostSummary, PlanStatus, PreferenceScoreCard } from './components/ResultSummary'
import { ExtractionSummary } from './components/ExtractionSummary'
import { ItineraryView } from './components/ItineraryView'
import { PlanningPipeline } from './components/PlanningPipeline'
import { ReplanningTimeline } from './components/ReplanningTimeline'
import { RequestForm } from './components/RequestForm'
import { RecentPlans } from './components/RecentPlans'
import { ValidationPanel } from './components/ValidationPanel'
import { AgentActivity } from './components/AgentActivity'
import { Card } from './components/ui'
import { label } from './lib/format'

const defaultQuery = 'I want to travel from Mumbai to Goa for 4 days with a maximum budget of ₹30,000. I prefer beaches, direct flights and a comfortable hotel.'

interface DisplayError {
  message: string
  clarification: ClarificationDetail | null
}

function App() {
  const [query, setQuery] = useState(defaultQuery)
  const [response, setResponse] = useState<NaturalLanguagePlanResponse | null>(null)
  const [restoredState, setRestoredState] = useState<TripState | null>(null)
  const [error, setError] = useState<DisplayError | null>(null)
  const [loading, setLoading] = useState(false)
  const [history, setHistory] = useState<PlanningSessionSummary[]>([])
  const [historyLoading, setHistoryLoading] = useState(true)
  const [openingId, setOpeningId] = useState<string | null>(null)

  const refreshHistory = useCallback(async () => {
    try {
      setHistory((await fetchRecentPlans()).sessions)
    } catch {
      // Planning remains usable if history is temporarily unavailable.
    } finally {
      setHistoryLoading(false)
    }
  }, [])

  useEffect(() => { void refreshHistory() }, [refreshHistory])

  async function plan() {
    setLoading(true)
    setError(null)
    try {
      setResponse(await planNaturalLanguageTrip(query.trim()))
      setRestoredState(null)
      void refreshHistory()
    } catch (caught) {
      if (caught instanceof TripMindApiError) {
        setError({ message: caught.message, clarification: caught.clarification })
      } else {
        setError({ message: 'Something unexpected interrupted planning. Please try again.', clarification: null })
      }
      setResponse(null)
      setRestoredState(null)
    } finally {
      setLoading(false)
    }
  }

  async function openSavedPlan(sessionId: string) {
    setOpeningId(sessionId)
    setError(null)
    try {
      const saved = await fetchPlanningSession(sessionId)
      setResponse(saved.response)
      setRestoredState(saved.response ? null : saved.result)
      setQuery(saved.query)
      window.scrollTo({ top: 0, behavior: 'smooth' })
    } catch (caught) {
      const message = caught instanceof TripMindApiError ? caught.message : 'The saved plan could not be opened.'
      setError({ message, clarification: null })
    } finally {
      setOpeningId(null)
    }
  }

  const state = response?.result ?? restoredState

  return (
    <main className="min-h-screen bg-[#f5f7f8] text-slate-900">
      <header className="border-b border-slate-200 bg-white/90 px-5 py-4 backdrop-blur sm:px-8">
        <div className="mx-auto flex max-w-7xl items-center justify-between gap-5">
          <div className="flex items-center gap-3">
            <div className="grid h-10 w-10 place-items-center rounded-xl bg-teal-500 text-xl font-black text-slate-950">T</div>
            <div><h1 className="text-xl font-black tracking-tight">TripMind</h1><p className="text-xs text-slate-500">Agentic Constraint-Aware Travel Planner</p></div>
          </div>
          <div className="hidden items-center gap-2 text-xs font-semibold text-slate-500 sm:flex"><span className="h-2 w-2 rounded-full bg-emerald-500" />Typed agent · Deterministic validation</div>
        </div>
      </header>

      <div className="mx-auto max-w-7xl px-4 py-8 sm:px-8 sm:py-12">
        <div className="mb-8 max-w-3xl">
          <p className="text-sm font-bold uppercase tracking-[0.24em] text-teal-700">Plan · validate · repair · explain</p>
          <h2 className="mt-3 text-4xl font-black tracking-tight text-slate-950 sm:text-5xl">Travel planning you can inspect.</h2>
          <p className="mt-4 max-w-2xl text-lg leading-relaxed text-slate-600">Local AI-assisted travel planning with deterministic validation and targeted replanning—built to make every constraint, correction, and trade-off visible.</p>
        </div>

        <RequestForm query={query} loading={loading} onQueryChange={setQuery} onSubmit={plan} />

        <RecentPlans sessions={history} loading={historyLoading} openingId={openingId} onOpen={openSavedPlan} />

        {loading && (
          <div role="status" className="mt-6 flex items-center gap-4 rounded-2xl border border-teal-200 bg-teal-50 p-5 text-teal-950">
            <span className="h-6 w-6 animate-spin rounded-full border-2 border-teal-200 border-t-teal-700" />
            <div><p className="font-bold">Building your plan…</p><p className="mt-1 text-sm text-teal-800">TripMind is extracting constraints, selecting registered tools, validating the candidate, and replanning if required.</p></div>
          </div>
        )}

        {error && <ErrorPanel error={error} onRetry={plan} />}

        {state && (
          <div className="mt-8 space-y-6">
            <div className="rounded-2xl border border-slate-200 bg-white px-5 py-4 shadow-sm">
              <p className="text-xs font-bold uppercase tracking-wider text-slate-400">Original query</p>
              <p className="mt-1 text-sm font-medium text-slate-700">“{response?.original_query ?? state.original_request}”</p>
            </div>
            <PlanStatus state={state} />
            {response ? (
              <div className="grid items-start gap-6 xl:grid-cols-[1.1fr_0.9fr]">
                <ExtractionSummary response={response} />
                <PlanningPipeline response={response} />
              </div>
            ) : (
              <Card>
                <p className="text-xs font-bold uppercase tracking-[0.18em] text-teal-700">Restored structured request</p>
                <p className="mt-2 text-sm text-slate-600">This saved API result has been reopened from SQLite. Its authoritative TripState is rendered below.</p>
              </Card>
            )}
            <AgentActivity state={state} />
            <ReplanningTimeline state={state} />
            {state.current_validation && <ValidationPanel validation={state.current_validation} />}
            {state.status === 'infeasible' && state.current_validation && state.current_validation.violations.length > 0 && (
              <Card className="border-amber-200 bg-amber-50/50">
                <p className="text-xs font-bold uppercase tracking-[0.18em] text-amber-700">Unresolved outcome</p>
                <h2 className="mt-1 text-xl font-bold">Why planning stopped</h2>
                <p className="mt-2 text-sm text-slate-600">The bounded policy used all {state.replanning_attempts.length} recorded attempt{state.replanning_attempts.length === 1 ? '' : 's'} without finding a legal improvement.</p>
              </Card>
            )}
            {state.current_itinerary && (
              <div className="grid items-start gap-6 xl:grid-cols-[1.35fr_0.65fr]">
                <ItineraryView itinerary={state.current_itinerary} final={state.status === 'completed'} />
                <div className="space-y-6">
                  <CostSummary costs={state.current_itinerary.costs} />
                  {state.preference_score && <PreferenceScoreCard score={state.preference_score} />}
                </div>
              </div>
            )}
            {state.final_explanation && <ExplanationPanel explanation={state.final_explanation} />}
          </div>
        )}

        <footer className="mt-12 flex flex-wrap items-center justify-between gap-3 border-t border-slate-200 pt-6 text-xs text-slate-400"><span>TripMind academic demo · No booking</span><span>Typed sources · Exact cost engine · Bounded replanning</span></footer>
      </div>
    </main>
  )
}

function ErrorPanel({ error, onRetry }: { error: DisplayError; onRetry: () => void }) {
  const clarification = error.clarification
  return (
    <div role="alert" className={`mt-6 rounded-2xl border p-6 ${clarification ? 'border-amber-200 bg-amber-50' : 'border-rose-200 bg-rose-50'}`}>
      <p className={`text-xs font-extrabold uppercase tracking-[0.18em] ${clarification ? 'text-amber-700' : 'text-rose-700'}`}>{clarification ? 'Clarification required' : 'Planning error'}</p>
      <div className="mt-2 flex flex-wrap items-start justify-between gap-4">
        <div><h2 className="text-xl font-bold text-slate-950">{clarification ? 'TripMind needs a little more information.' : 'The request could not be completed.'}</h2><p className="mt-1 text-sm text-slate-600">{error.message}</p></div>
        {!clarification && <button type="button" onClick={onRetry} className="rounded-xl bg-rose-700 px-4 py-2 text-sm font-bold text-white hover:bg-rose-600">Retry</button>}
      </div>
      {clarification && <div className="mt-4 flex flex-wrap gap-2">{clarification.missing_fields.map((field) => <span key={field} className="rounded-full border border-amber-200 bg-white px-3 py-1.5 text-sm font-semibold text-amber-900">Missing {label(field).toLowerCase()}</span>)}</div>}
      {clarification?.warnings.map((warning) => <p key={warning} className="mt-3 text-xs text-amber-800">{warning}</p>)}
    </div>
  )
}

export default App
