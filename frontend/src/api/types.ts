// Mirrors backend/app/contracts.py (the source of truth). Only the fields the UI reads.
// `npm run gen:types` generates the full OpenAPI types into openapi.d.ts when the backend is running.

export type Fuel = 'DIESEL' | 'PETROL' | 'OCTANE';
export const FUELS: Fuel[] = ['DIESEL', 'PETROL', 'OCTANE'];
export type Litres = Partial<Record<Fuel, number>>;

export interface Region { id: string; name: string; demand_factor: number }
export interface Depot {
  id: string; name: string; region_id: string; status: 'OPEN' | 'CONSTRAINED' | 'CLOSED';
  dispatch_capacity_per_tick: number; capacity: Litres; inventory: Litres;
}
export interface Station {
  id: string; name: string; region_id: string; status: 'OPEN' | 'OUTAGE';
  demand_profile: string; demand_multiplier: number; capacity: Litres; inventory: Litres;
}
export interface Route {
  id: string; source_depot_id: string; destination_station_id: string;
  transit_ticks: number; max_shipment: number; status: 'AVAILABLE' | 'DISRUPTED';
}
export interface SupplyArrival {
  id: string; depot_id: string; fuel_type: Fuel; quantity: number;
  planned_tick: number; actual_tick: number | null; status: 'SCHEDULED' | 'DELAYED' | 'ARRIVED' | 'CANCELLED';
}
export interface SimEvent {
  id: number; type: string; start_tick: number; end_tick: number;
  status: 'SCHEDULED' | 'ACTIVE' | 'RESOLVED'; parameters: Record<string, unknown>;
}
export interface InTransitLeg {
  allocation_id: number; route_id: string; source_depot_id: string; station_id: string;
  fuel_type: Fuel; quantity: number; status: 'PENDING' | 'IN_TRANSIT'; expected_arrival_tick: number | null;
}
export interface ResourceFreshness { fetched_at: string | null; age_seconds: number | null; stale: boolean; last_error: string | null }
export interface Freshness {
  stale: boolean; reasons: string[]; circuit: 'CLOSED' | 'OPEN' | 'HALF_OPEN';
  resources: Record<string, ResourceFreshness>;
}
export interface SimMetrics {
  served_demand_liters: number; unmet_demand_liters: number; service_level: number;
  allocation_liters: number; allocation_failures: number;
}
export interface NetworkSnapshot {
  tick: number; sim_time: string | null; tick_minutes: number; sim_status: 'PAUSED' | 'RUNNING';
  scenario_id: string; seed: number; built_at: string | null; freshness: Freshness | null;
  regions: Region[]; depots: Depot[]; stations: Station[]; routes: Route[];
  supply_arrivals: SupplyArrival[]; events: SimEvent[]; in_transit: InTransitLeg[];
  metrics: SimMetrics | null; in_transit_totals: Record<string, Litres>; dispatched_this_tick: Record<string, number>;
}

export interface AllocationLeg {
  route_id: string; source_depot_id: string; station_id: string; fuel_type: Fuel; quantity: number; transit_ticks?: number;
}
export interface Signal {
  kind: string; severity: 'info' | 'warn' | 'crit'; message: string;
  station_id: string | null; fuel_type: Fuel | null; value: number | null;
}
export interface RiskItem {
  station_id: string; fuel_type: Fuel; hours_to_stockout: number | null; p_stockout: number;
  has_backup_route: boolean; current_inventory?: number; severity?: string;
}
export interface TwinFuture {
  candidate_id: string; label: string; name: string; horizon_ticks: number; network_unmet_liters: number;
  unmet_by_station: Record<string, number>; first_stockout_tick: number | null; service_level: number;
  notes: string[] | string; legs: AllocationLeg[];
}
export interface Candidate { id: string; policy: string; legs: AllocationLeg[] }
export interface Recommendation {
  id: string; tick: number; created_at: string; mode: 'prevention' | 'containment';
  candidates: Candidate[]; selected_candidate_id: string; futures: TwinFuture[]; twin_futures: TwinFuture[];
  risks: RiskItem[]; signals: Signal[]; constraints: string[]; constraints_applied: string[];
  confidence: number; versions: Record<string, string>; fallback_used: string[]; built_on_stale_data: boolean;
  legs: AllocationLeg[]; policy: string; alternatives: string[]; human_review_required: boolean; status: string;
}

export type Mode = 'MANUAL' | 'SUPERVISED' | 'AUTONOMOUS';
export interface Gate {
  // Records created by other lanes may carry a partial gate; the UI treats missing fields as unknown.
  requires_human: boolean; executable: boolean; auto_execute: boolean; reasons: string[];
  blocked_legs: { index: number; route_id: string; reason: string }[]; mode: Mode; confidence: number;
}
export interface Factor { key: string; label: string; weight: number; value: number | null }
export interface Autonomy {
  mode: Mode; armed: boolean; healthy_ticks: number; confidence: number; target_mode: Mode | null;
  factors: Factor[]; thresholds: { autonomous_min: number; manual_below: number; healthy_ticks_to_climb: number };
  log: { tick: number | null; message: string }[];
  autopilot?: boolean;
}
export interface CurrentView {
  tick: number; source: string; error: string | null; recommendation: Recommendation | null;
  gate: Gate | null; record_stage: string | null; autonomy: Autonomy;
}

export interface SubmittedAllocation {
  leg: AllocationLeg; idempotency_key: string; http_status: number | null; sim_allocation_id: number | null;
  result: 'accepted' | 'rejected' | 'held' | 'skipped'; error_code: string | null; message: string | null;
}
export interface DecisionRecord {
  decision_id: string; sim_tick: number; created_at: string; stage: string; versions: Record<string, string>;
  mode: string | null; recommendation: Recommendation | null; gate: Gate | null;
  approval: { decision: string; by: string; reason: string | null; modified?: boolean; at?: string; candidate_id?: string | null } | null;
  submissions: SubmittedAllocation[]; outcome: Record<string, unknown> | null;
  twin_check: { predicted_l: number; actual_l: number; error_l: number } | null;
  observed: Record<string, unknown> | null; prediction: Record<string, unknown> | null;
  candidates: { id: string; label: string; unmet_l: number }[];
}

export interface ComponentHealth { name: string; status: 'healthy' | 'degraded' | 'down' | 'unknown'; detail: string | null }
export interface HealthReport {
  status: 'healthy' | 'degraded' | 'down'; components: ComponentHealth[]; tick: number | null; snapshot_age_seconds: number | null;
  version?: string; active_policy?: string | null; pacer_running?: boolean;
}
export interface ExplainResponse { text: string; cited_facts: string[]; source: 'llm' | 'template'; confidence: number; llm_model: string }

export interface RLRecommendation {
  source_depot: string;
  destination_station: string;
  fuel_type: Fuel;
  quantity: number;
  route: string;
}

export interface RLRecommendResponse {
  recommendation: RLRecommendation | null;
  model: { name: string; version: string };
  confidence: number;
  status: 'pending_human_review' | 'rejected_by_guardrails' | string;
  reason?: string;
  latency_ms: number;
}

export interface RLStatsResponse {
  model_name: string;
  model_version: string;
  loaded: boolean;
  status: 'healthy' | 'degraded' | 'down';
  detail: string;
  observation_dim: number;
  model_path: string;
}

export interface RAGSourceCitation {
  source: string;
  document_id: string;
  section: string | null;
  page: number | null;
  category: string;
  score: number;
}

export interface RAGSearchResult {
  chunk_id: string;
  document_id: string;
  source: string;
  category: string;
  section: string | null;
  content: string;
  score: number;
  dense_score?: number;
  sparse_score?: number;
}

export interface RAGSearchResponse {
  results: RAGSearchResult[];
}

export interface RAGAskResponse {
  answer: string;
  sources: RAGSourceCitation[];
  results: RAGSearchResult[];
  mode?: 'llm' | 'extractive';
}

export interface RAGStatsResponse {
  status: 'healthy' | 'degraded' | 'down';
  detail: string;
  counts: {
    total_documents: number;
    total_chunks: number;
    categories: Record<string, number>;
  };
  data_dir: string;
  offline_mode: boolean;
}
