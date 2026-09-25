export type PlanningStatus =
  | 'received'
  | 'searching'
  | 'candidate_ready'
  | 'validating'
  | 'replanning'
  | 'completed'
  | 'infeasible'
  | 'failed'

export type TransportMode = 'flight' | 'train' | 'bus' | 'car' | 'walk'
export type ItemType = 'flight' | 'hotel' | 'activity' | 'route'

export interface TravelConstraints {
  origin_city_id: string
  destination_city_id: string
  additional_destination_city_ids: string[]
  start_date: string
  end_date: string
  travelers: number
  total_budget: string
  max_flight_price: string | null
  max_hotel_price_per_night: string | null
  max_travel_duration_minutes: number | null
  allowed_transport_modes: TransportMode[]
}

export interface SoftPreferences {
  preferred_transport_modes: TransportMode[]
  preferred_airlines: string[]
  preferred_hotel_amenities: string[]
  preferred_activity_categories: string[]
  prefer_direct_flights: boolean
  pace: 'relaxed' | 'balanced' | 'packed' | null
  notes: string | null
}

export interface TravelRequest {
  request_id: string
  natural_language_request: string
  constraints: TravelConstraints
  preferences: SoftPreferences
}

export interface ItineraryItem {
  id: string
  item_type: ItemType
  option_id: string
  title: string
  starts_at: string
  ends_at: string
  cost: string
  city_id: string | null
  origin_city_id: string | null
  destination_city_id: string | null
  duration_minutes: number | null
  notes: string | null
}

export interface ItineraryDay {
  date: string
  items: ItineraryItem[]
}

export interface CostBreakdown {
  flights: string
  hotels: string
  activities: string
  local_transport: string
  other: string
  currency: string
  total: string
}

export interface Itinerary {
  id: string
  days: ItineraryDay[]
  costs: CostBreakdown
}

export interface ConstraintCheck {
  constraint: string
  passed: boolean
  expected: string
  actual: string
  violation_code: string | null
}

export interface ConstraintViolation {
  code: string
  message: string
  item_id: string | null
  details: Record<string, unknown>
}

export interface ValidationResult {
  is_valid: boolean
  feasible: boolean
  checks: ConstraintCheck[]
  violations: ConstraintViolation[]
  validated_at: string | null
}

export interface CorrectiveAction {
  action: string
  reason: string
  target_id: string | null
  target_violation: string
  expected_goal: string
  parameters: Record<string, unknown>
}

export interface ReplanningAttempt {
  attempt_number: number
  triggered_by: string[]
  actions: CorrectiveAction[]
  created_at: string
  succeeded: boolean | null
  before_component_id: string | null
  after_component_id: string | null
  before_value: string | number | null
  after_value: string | number | null
  cost_effect: string | null
  duration_effect_minutes: number | null
  tool_name: string | null
  validation_result: ValidationResult | null
  outcome: string | null
  state_fingerprint: string | null
}

export interface ToolCallRecord {
  id: string
  tool_name: string
  arguments: Record<string, unknown>
  status: 'succeeded' | 'failed'
  result_count: number | null
  started_at: string
  completed_at: string | null
  error: string | null
}

export interface PreferenceScore {
  total: string
  components: Record<string, string>
  weights: Record<string, string>
  explanation: string[]
}

export interface TripState {
  trip_id: string
  original_request: string
  constraints: TravelConstraints
  preferences: SoftPreferences
  status: PlanningStatus
  initial_itinerary: Itinerary | null
  current_itinerary: Itinerary | null
  current_validation: ValidationResult | null
  validation_history: ValidationResult[]
  replanning_attempts: ReplanningAttempt[]
  tool_call_history: ToolCallRecord[]
  preference_score: PreferenceScore | null
  final_explanation: string | null
}

export interface NaturalLanguagePlanResponse {
  original_query: string
  extracted_request: TravelRequest
  extraction: {
    requested_provider: string
    provider_used: string
    fallback_used: boolean
    fallback_reason: string | null
    assumptions: string[]
    warnings: string[]
  }
  sources: {
    planning: string
    travel_source: string
    validation: string
    replanning: string
  }
  result: TripState
}

export interface ClarificationDetail {
  code: 'clarification_required'
  message: string
  missing_fields: string[]
  warnings: string[]
  requested_provider: string
  provider_used: string
  fallback_used: boolean
  fallback_reason: string | null
}

export interface PlanningSessionSummary {
  id: string
  created_at: string
  query: string
  origin_city_id: string
  destination_city_ids: string[]
  status: PlanningStatus
  initial_cost: string | null
  final_cost: string | null
  preference_score: string | null
}

export interface PlanningHistoryResponse {
  sessions: PlanningSessionSummary[]
}

export interface PlanningSessionDetail extends PlanningSessionSummary {
  provider_metadata: Record<string, unknown>
  request_payload: Record<string, unknown>
  result: TripState
  response: NaturalLanguagePlanResponse | null
}
