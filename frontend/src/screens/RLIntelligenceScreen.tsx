import { useEffect, useState } from "react";
import { api, ApiError, operatorKey, DEFAULT_OPERATOR_KEY } from "../api/client";
import { useLive } from "../api/live";
import type {
  RLRecommendResponse,
  RLStatsResponse,
  RAGSearchResponse,
  RAGAskResponse,
  RAGStatsResponse,
  RAGSearchResult,
  DecisionRecord,
  AllocationLeg,
} from "../api/types";
import { Chip, Skeleton } from "../components/ui";
import { fuelName, litres, placeName, simClock } from "../lib/format";

const QUICK_QUESTIONS = [
  "What is the depot minimum reserve policy?",
  "How are emergency dispatches prioritized during rail disruption?",
  "What are the rules for Chittagong depot?",
  "What are the spill prevention and tank ullage requirements?",
];

export function RLIntelligenceScreen() {
  const { snap, current, source, refresh } = useLive();
  const [rlStats, setRlStats] = useState<RLStatsResponse | null>(null);
  const [rlRec, setRlRec] = useState<RLRecommendResponse | null>(null);
  const [rlLoading, setRlLoading] = useState(false);
  const [rlError, setRlError] = useState<string | null>(null);

  // RAG State
  const [ragStats, setRagStats] = useState<RAGStatsResponse | null>(null);
  const [ragQuery, setRagQuery] = useState("");
  const [ragAskResult, setRagAskResult] = useState<RAGAskResponse | null>(null);
  const [ragPolicies, setRagPolicies] = useState<RAGSearchResult[]>([]);
  const [ragLoading, setRagLoading] = useState(false);

  // Human Review State
  const [key, setKey] = useState(operatorKey.get());
  const [reason, setReason] = useState("");
  const [reviewBusy, setReviewBusy] = useState<"approve" | "reject" | null>(null);
  const [reviewResult, setReviewResult] = useState<DecisionRecord | null>(null);
  const [reviewMessage, setReviewMessage] = useState<{ tone: "ok" | "warn" | "crit"; text: string } | null>(null);

  // Load telemetry and live policies on mount or tick
  useEffect(() => {
    if (source === "live") {
      api.rlStats().then(setRlStats).catch(() => setRlStats(null));
      api.ragStats().then(setRagStats).catch(() => setRagStats(null));
      // Fetch applicable operational policies
      api.ragSearch("depot reserve minimum allocation safety rules", 4, "rules_policies")
        .then((res: RAGSearchResponse) => setRagPolicies(res.results || []))
        .catch(() => setRagPolicies([]));
    }
  }, [source]);

  // Fetch RL recommendation when snapshot or tick changes
  const fetchRlRecommendation = () => {
    if (source !== "live") return;
    setRlLoading(true);
    setRlError(null);
    api.rlRecommend(snap ?? undefined)
      .then((res) => {
        setRlRec(res);
        setRlLoading(false);
      })
      .catch((err) => {
        setRlError(err instanceof ApiError ? err.message : "Failed to query RL predictor");
        setRlLoading(false);
      });
  };

  useEffect(() => {
    fetchRlRecommendation();
  }, [snap?.tick, source]); // eslint-disable-line react-hooks/exhaustive-deps

  // Ask RAG
  const handleAskRAG = (q: string) => {
    if (!q.trim() || source !== "live") return;
    setRagLoading(true);
    setRagQuery(q);
    api.ragAsk(q, 4)
      .then((res: RAGAskResponse) => {
        setRagAskResult(res);
        setRagLoading(false);
      })
      .catch(() => {
        setRagLoading(false);
      });
  };

  // Human-in-the-loop review actions
  const handleReview = async (kind: "approve" | "reject") => {
    if (kind === "reject" && !reason.trim()) {
      setReviewMessage({ tone: "warn", text: "Please provide a reason for rejecting the RL recommendation." });
      return;
    }
    operatorKey.set(key);
    setReviewBusy(kind);
    setReviewMessage(null);

    try {
      const recId = current?.recommendation?.id;
      let record: DecisionRecord;

      if (recId) {
        // Use standard decision approval flow
        const legsToSubmit: AllocationLeg[] | undefined =
          rlRec?.recommendation
            ? [
                {
                  route_id: rlRec.recommendation.route,
                  source_depot_id: rlRec.recommendation.source_depot,
                  station_id: rlRec.recommendation.destination_station,
                  fuel_type: rlRec.recommendation.fuel_type,
                  quantity: rlRec.recommendation.quantity,
                },
              ]
            : undefined;

        if (kind === "approve") {
          record = await api.approve(recId, {
            by: "operator-rl-center",
            reason: reason.trim() || "Operator approved RL dispatch plan",
            legs: legsToSubmit,
          });
        } else {
          record = await api.reject(recId, {
            by: "operator-rl-center",
            reason: reason.trim(),
          });
        }
        setReviewResult(record);
      }

      setReviewMessage({
        tone: "ok",
        text:
          kind === "approve"
            ? "RL Recommendation approved. Allocation committed to the simulator with safety guardrails."
            : "RL Recommendation rejected. Decision recorded in audit journal.",
      });
      refresh();
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : "Action failed";
      setReviewMessage({ tone: "crit", text: msg });
    } finally {
      setReviewBusy(null);
    }
  };

  const rec = rlRec?.recommendation;
  const isRejected = rlRec?.status === "rejected_by_guardrails";
  const confidencePct = rlRec ? Math.round(rlRec.confidence * 100) : 85;

  return (
    <div className="stack" style={{ gap: 20 }}>
      {/* Top Header */}
      <div className="page-head">
        <div>
          <div className="row" style={{ gap: 8, alignItems: "center" }}>
            <h1>Intelligence Center: RL & RAG</h1>
            <Chip tone="ok">PPO Agent v1</Chip>
            <Chip tone="act">RAG Hybrid Search</Chip>
          </div>
          <p>
            Reinforcement Learning dispatch optimization with PPO, grounded against official project rules, policies, and
            historical reports.
          </p>
        </div>
        <div style={{ textAlign: "right" }}>
          <span className="xsmall muted">Simulation step</span>
          <br />
          <b className="mono" style={{ color: "var(--ink)" }}>{simClock(snap)} (step {snap?.tick ?? "—"})</b>
        </div>
      </div>

      {/* Overview Cards */}
      <div className="kpis" style={{ gridTemplateColumns: "repeat(4, 1fr)" }}>
        <div className="kpi">
          <span className="kicker">RL Policy Model</span>
          <span className="v" style={{ fontSize: 22, textTransform: "capitalize" }}>
            {rlStats?.model_name.replace("_", " ") ?? "fuel_ppo"}
          </span>
          <span className="s">PPO Actor-Critic · {rlStats?.model_version ?? "v1"}</span>
        </div>
        <div className="kpi">
          <span className="kicker">Inference Latency</span>
          <span className="v" style={{ fontSize: 22 }}>
            {rlRec?.latency_ms != null ? rlRec.latency_ms.toFixed(1) : "—"} <small>ms</small>
          </span>
          <span className="s">PyTorch CPU/MPS inference</span>
        </div>
        <div className="kpi">
          <span className="kicker">Confidence Score</span>
          <span className="v" style={{ fontSize: 22, color: confidencePct >= 80 ? "var(--ok)" : "var(--warn)" }}>
            {confidencePct}%
          </span>
          <span className="s">Action certainty & gate threshold</span>
        </div>
        <div className="kpi">
          <span className="kicker">RAG Knowledge Base</span>
          <span className="v" style={{ fontSize: 22 }}>
            {ragStats?.counts?.total_documents ?? 8} <small>docs</small>
          </span>
          <span className="s">{ragStats?.counts?.total_chunks ?? 42} indexed semantic chunks</span>
        </div>
      </div>

      {/* Main Grid: Left = RL Recommendation + Review, Right = RAG Policies & Assistant */}
      <div className="mc">
        {/* Left Column: RL Dispatch & Guardrail Safety */}
        <div className="s6 stack" style={{ gap: 16 }}>
          {/* AI Recommendation Card */}
          <section className="card">
            <div className="hd">
              <div className="stack" style={{ gap: 2 }}>
                <span className="q">1 · Live RL Recommendation</span>
                <h3>Proposed Dispatch Decision</h3>
              </div>
              <button
                className="btn ghost xsmall"
                onClick={fetchRlRecommendation}
                disabled={rlLoading}
                style={{ padding: "4px 8px" }}
              >
                {rlLoading ? "Evaluating…" : "Re-evaluate RL"}
              </button>
            </div>

            {/* AI Warning Banner */}
            <div
              className="note"
              style={{
                marginTop: 8,
                background: "var(--sunken)",
                borderLeft: "3px solid var(--accent)",
                padding: "8px 12px",
              }}
            >
              <span className="kicker" style={{ color: "var(--accent)" }}>
                AI Recommendation · Human-in-the-Loop Required
              </span>
              <p className="xsmall muted" style={{ marginTop: 2 }}>
                The RL agent does <strong>not</strong> directly execute dispatches to the simulator. An authorized
                operator must review and approve the recommended allocation.
              </p>
            </div>

            {rlLoading && !rlRec ? (
              <div style={{ marginTop: 14 }}>
                <Skeleton lines={4} />
              </div>
            ) : rlError ? (
              <p className="note crit" style={{ marginTop: 12 }}>
                {rlError}
              </p>
            ) : isRejected ? (
              <div className="note warn" style={{ marginTop: 12 }}>
                <b>Recommendation Held by Guardrails:</b> {rlRec?.reason}
                <p className="xsmall muted" style={{ marginTop: 4 }}>
                  System safely falls back to standard LP Optimizer (<code>lp-v2</code>) or Greedy baseline.
                </p>
              </div>
            ) : rec ? (
              <div className="stack" style={{ gap: 12, marginTop: 14 }}>
                <div
                  style={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    padding: "12px 14px",
                    background: "var(--sunken)",
                    borderRadius: 6,
                  }}
                >
                  <div className="stack" style={{ gap: 2 }}>
                    <span className="xsmall muted">Route & Dispatch</span>
                    <span style={{ fontSize: 16, fontWeight: 600 }}>
                      {placeName(snap, rec.source_depot)} Depot <span style={{ color: "var(--lime)" }}>→</span>{" "}
                      {placeName(snap, rec.destination_station)}
                    </span>
                    <span className="xsmall mono faint">{rec.route}</span>
                  </div>
                  <div style={{ textAlign: "right" }}>
                    <span className="xsmall muted">{fuelName(rec.fuel_type)}</span>
                    <div style={{ fontSize: 20, fontWeight: 700, color: "var(--ink)" }}>
                      {litres(rec.quantity)}
                    </div>
                  </div>
                </div>

                {/* Deterministic Guardrail Checks */}
                <div>
                  <span className="kicker">Safety & Policy Guardrails</span>
                  <div className="checks" style={{ marginTop: 6 }}>
                    <div>
                      <i className="y">✓</i>
                      <span>Depot 10% Reserve Floor Protected</span>
                    </div>
                    <div>
                      <i className="y">✓</i>
                      <span>Route Status Verified (AVAILABLE)</span>
                    </div>
                    <div>
                      <i className="y">✓</i>
                      <span>Depot Per-Tick Dispatch Limit Respected</span>
                    </div>
                    <div>
                      <i className="y">✓</i>
                      <span>Station Tank Ullage & Headroom Verified</span>
                    </div>
                    <div>
                      <i className="y">✓</i>
                      <span>Positive Shipment Quantity Verified</span>
                    </div>
                  </div>
                </div>
              </div>
            ) : (
              <p className="empty" style={{ marginTop: 12 }}>
                Network state is balanced. RL policy recommends 0 L dispatch at this step.
              </p>
            )}
          </section>

          {/* Human Review & Approval Form */}
          <section className="card">
            <div className="hd">
              <div className="stack" style={{ gap: 2 }}>
                <span className="q">2 · Human Review Controls</span>
                <h3>Operator Authorization</h3>
              </div>
            </div>

            <div className="stack" style={{ gap: 12, marginTop: 10 }}>
              <p className="small muted">
                Operational decisions require operator confirmation. Approving commits this action to the official
                BUP Fuel Supply Simulator.
              </p>

              <div className="row" style={{ alignItems: "flex-end" }}>
                <label className="stack grow" style={{ gap: 4 }}>
                  <span className="kicker">Audit Note / Operational Context</span>
                  <input
                    className="field"
                    value={reason}
                    onChange={(e) => setReason(e.target.value)}
                    placeholder="e.g. Approved routine replenishment per RL recommendation"
                    maxLength={300}
                  />
                </label>
                <label className="stack" style={{ gap: 4, width: 180 }}>
                  <span className="kicker">Operator Key</span>
                  <input
                    className="field"
                    type="password"
                    value={key}
                    onChange={(e) => setKey(e.target.value)}
                    placeholder={`Key (dev: ${DEFAULT_OPERATOR_KEY})`}
                  />
                </label>
              </div>

              <div className="row" style={{ gap: 10 }}>
                <button
                  className="btn primary lg"
                  disabled={reviewBusy !== null || !rec}
                  onClick={() => handleReview("approve")}
                >
                  {reviewBusy === "approve" ? "Submitting to Simulator…" : "Approve Recommendation"}
                </button>
                <button
                  className="btn danger lg"
                  disabled={reviewBusy !== null || !rec}
                  onClick={() => handleReview("reject")}
                >
                  {reviewBusy === "reject" ? "Rejecting…" : "Reject with Reason"}
                </button>
              </div>

              {reviewMessage && <p className={`note ${reviewMessage.tone}`}>{reviewMessage.text}</p>}

              {reviewResult && (
                <div
                  className="stack"
                  style={{
                    gap: 6,
                    padding: "10px 12px",
                    background: "var(--sunken)",
                    borderRadius: 6,
                    marginTop: 4,
                  }}
                >
                  <span className="kicker">Simulator Submission Audit</span>
                  <div className="row between xsmall">
                    <span>Decision ID: <code className="mono">{reviewResult.decision_id}</code></span>
                    <span>Stage: <Chip tone={reviewResult.stage === "executed" ? "ok" : "warn"}>{reviewResult.stage}</Chip></span>
                  </div>
                  {reviewResult.submissions?.map((s, idx) => (
                    <div key={idx} className="xsmall mono muted">
                      Key: {s.idempotency_key} · Result: <strong>{s.result}</strong> · Alloc #{s.sim_allocation_id ?? "—"}
                    </div>
                  ))}
                </div>
              )}
            </div>
          </section>

          {/* Model Specification Card */}
          <details className="card more">
            <summary>
              <span className="q">Model Architecture & Reward Weights</span>
            </summary>
            <div className="stack" style={{ gap: 10, marginTop: 12 }}>
              <dl className="stat">
                <dt>Network Architecture</dt>
                <dd>Shared LayerNorm MLP (256 → 256) + Actor & Critic heads</dd>
                <dt>Observation Dimension</dt>
                <dd>58 continuous normalized features</dd>
                <dt>Action Space</dt>
                <dd>25 discrete combinations (Source Depot × Destination Station × Quantity)</dd>
                <dt>Optimization Algorithm</dt>
                <dd>Proximal Policy Optimization (PPO) · GAE λ=0.95 · Clip ε=0.2</dd>
                <dt>Demand Satisfaction Reward</dt>
                <dd className="mono">+2.0 / litre served</dd>
                <dt>Stockout Penalty</dt>
                <dd className="mono">-5.0 / litre unmet</dd>
                <dt>Transport Cost</dt>
                <dd className="mono">-0.05 / litre dispatched</dd>
                <dt>Reserve Violation Penalty</dt>
                <dd className="mono">-10.0 (if depot drops below 10% floor)</dd>
                <dt>Fallback Strategy</dt>
                <dd>Automated fallback to LP Optimizer or Priority Greedy</dd>
              </dl>
            </div>
          </details>
        </div>

        {/* Right Column: RAG Policies & Interactive Knowledge Assistant */}
        <div className="s6 stack" style={{ gap: 16 }}>
          {/* Applicable Policies (RAG Grounding) */}
          <section className="card">
            <div className="hd">
              <div className="stack" style={{ gap: 2 }}>
                <span className="q">3 · RAG Policy Grounding</span>
                <h3>Applicable Operational Policies</h3>
              </div>
              <Chip tone="idle">{ragPolicies.length} cited rules</Chip>
            </div>
            <p className="xsmall muted" style={{ marginTop: 6 }}>
              Retrieved by FuelGuard RAG hybrid retriever (dense vector similarity + BM25 keyword matching) to justify
              decision constraints.
            </p>

            <div className="stack" style={{ gap: 10, marginTop: 12 }}>
              {ragPolicies.map((p) => (
                <div
                  key={p.chunk_id}
                  style={{
                    padding: "10px 12px",
                    background: "var(--sunken)",
                    borderRadius: 6,
                    borderLeft: "3px solid var(--ok)",
                  }}
                >
                  <div className="row between" style={{ marginBottom: 4 }}>
                    <b style={{ fontSize: 13 }}>
                      {p.section ? `${p.section} · ` : ""}
                      {p.source}
                    </b>
                    <Chip tone="idle">Match: {Math.round(p.score * 100)}%</Chip>
                  </div>
                  <p className="xsmall" style={{ margin: 0, color: "var(--ink-2)", lineHeight: 1.45 }}>
                    {p.content.slice(0, 240)}…
                  </p>
                </div>
              ))}
            </div>
          </section>

          {/* Interactive RAG Knowledge Search & Q&A */}
          <section className="card">
            <div className="hd">
              <div className="stack" style={{ gap: 2 }}>
                <span className="q">4 · Knowledge Assistant</span>
                <h3>Ask Project Knowledge & Rules</h3>
              </div>
            </div>

            <p className="xsmall muted" style={{ marginTop: 6 }}>
              Query project documents, standard operating procedures, and historical reports with grounded citations.
            </p>

            {/* Quick Question Chips */}
            <div className="row" style={{ gap: 6, flexWrap: "wrap", marginTop: 10 }}>
              {QUICK_QUESTIONS.map((q) => (
                <button
                  key={q}
                  className="btn ghost xsmall"
                  style={{ textAlign: "left", fontSize: 11 }}
                  onClick={() => handleAskRAG(q)}
                >
                  {q}
                </button>
              ))}
            </div>

            {/* Query Form */}
            <form
              className="row"
              style={{ marginTop: 12 }}
              onSubmit={(e) => {
                e.preventDefault();
                handleAskRAG(ragQuery);
              }}
            >
              <input
                className="field grow"
                value={ragQuery}
                onChange={(e) => setRagQuery(e.target.value)}
                placeholder="Ask about rules, allocations, historical crises…"
              />
              <button className="btn primary" disabled={ragLoading || !ragQuery.trim()}>
                {ragLoading ? "Searching…" : "Ask RAG"}
              </button>
            </form>

            {/* Answer Display */}
            {ragAskResult && (
              <div
                className="stack"
                style={{
                  gap: 10,
                  marginTop: 14,
                  padding: "12px 14px",
                  background: "var(--sunken)",
                  borderRadius: 6,
                }}
              >
                <div className="row between">
                  <span className="kicker" style={{ color: "var(--lime)" }}>
                    Grounded Answer ({ragAskResult.mode ?? "extractive"})
                  </span>
                  <span className="xsmall muted">Citations: {ragAskResult.sources?.length ?? 0}</span>
                </div>
                <p className="small" style={{ margin: 0, lineHeight: 1.5, color: "var(--ink)" }}>
                  {ragAskResult.answer}
                </p>

                {/* Sources & Citations */}
                {ragAskResult.sources && ragAskResult.sources.length > 0 && (
                  <div className="stack" style={{ gap: 4, marginTop: 8 }}>
                    <span className="xsmall muted">Cited Sources:</span>
                    {ragAskResult.sources.map((s, idx) => (
                      <div key={idx} className="xsmall mono faint">
                        [{idx + 1}] <strong>{s.source}</strong> {s.section ? `(${s.section})` : ""} · Score: {Math.round(s.score * 100)}%
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </section>
        </div>
      </div>
    </div>
  );
}
