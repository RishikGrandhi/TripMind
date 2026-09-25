import type {
  ClarificationDetail,
  NaturalLanguagePlanResponse,
  PlanningHistoryResponse,
  PlanningSessionDetail,
} from './types'

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? 'http://127.0.0.1:8000').replace(/\/$/, '')

export class TripMindApiError extends Error {
  status: number | null
  clarification: ClarificationDetail | null

  constructor(message: string, status: number | null = null, clarification: ClarificationDetail | null = null) {
    super(message)
    this.name = 'TripMindApiError'
    this.status = status
    this.clarification = clarification
  }
}

export async function planNaturalLanguageTrip(
  query: string,
  signal?: AbortSignal,
): Promise<NaturalLanguagePlanResponse> {
  let response: Response
  try {
    response = await fetch(`${API_BASE_URL}/api/v1/trips/plan-natural`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query }),
      signal,
    })
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error
    throw new TripMindApiError('Could not reach the local TripMind backend. Make sure it is running on port 8000.')
  }

  const payload: unknown = await response.json().catch(() => null)
  if (!response.ok) {
    const detail = isRecord(payload) ? payload.detail : null
    if (response.status === 422 && isClarification(detail)) {
      throw new TripMindApiError(detail.message, response.status, detail)
    }
    const message = typeof detail === 'string'
      ? detail
      : isRecord(detail) && typeof detail.message === 'string'
        ? detail.message
        : `TripMind returned an unexpected error (${response.status}).`
    throw new TripMindApiError(message, response.status)
  }
  return payload as NaturalLanguagePlanResponse
}

export async function fetchRecentPlans(limit = 8): Promise<PlanningHistoryResponse> {
  return requestJson<PlanningHistoryResponse>(`/api/v1/trips/history?limit=${limit}`)
}

export async function fetchPlanningSession(sessionId: string): Promise<PlanningSessionDetail> {
  return requestJson<PlanningSessionDetail>(`/api/v1/trips/${encodeURIComponent(sessionId)}`)
}

async function requestJson<T>(path: string): Promise<T> {
  let response: Response
  try {
    response = await fetch(`${API_BASE_URL}${path}`)
  } catch {
    throw new TripMindApiError('Could not reach the local TripMind backend. Make sure it is running on port 8000.')
  }
  const payload: unknown = await response.json().catch(() => null)
  if (!response.ok) {
    const detail = isRecord(payload) ? payload.detail : null
    const message = typeof detail === 'string'
      ? detail
      : isRecord(detail) && typeof detail.message === 'string'
        ? detail.message
        : `TripMind returned an unexpected error (${response.status}).`
    throw new TripMindApiError(message, response.status)
  }
  return payload as T
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

function isClarification(value: unknown): value is ClarificationDetail {
  return isRecord(value)
    && value.code === 'clarification_required'
    && typeof value.message === 'string'
    && Array.isArray(value.missing_fields)
}
