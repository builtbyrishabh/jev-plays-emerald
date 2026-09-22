"use strict";

const PHASES = {
  idle: { label: "Watching", tone: "idle" },
  selected: { label: "Choice selected", tone: "selected" },
  pending: { label: "Thinking", tone: "pending" },
  error: { label: "Needs attention", tone: "error" },
  paused: { label: "Paused", tone: "paused" },
  completed: { label: "Mission complete", tone: "completed" },
  checkpoint: { label: "Checkpoint loaded", tone: "checkpoint" },
};

function buildViewModel(state) {
  const status = state?.status ?? {};
  const terminal = ["checkpoint", "completed"].includes(status.phase);
  const phaseName = status.phase === "error" || terminal ? status.phase : state?.paused ? "paused" : status.phase ?? "idle";
  const phase = PHASES[phaseName] ?? PHASES.idle;
  let detail = "Agent is observing the game";
  if (phaseName === "pending") {
    detail = `Jev request${status.pending_attempt ? ` · attempt ${status.pending_attempt}` : ""}`;
  } else if (phaseName === "error") {
    detail = status.last_error || "The run paused after an error";
  } else if (phaseName === "paused") {
    detail = "Inputs are neutral; resume creates a fresh observation";
  } else if (phaseName === "checkpoint") {
    detail = "The selected target was already achieved when this run started";
  } else if (phaseName === "completed") {
    detail = "Victory verified during this run";
  } else if (phaseName === "selected") {
    detail = status.last_decision?.source === "deterministic" ? "Deterministic action" : "Jev choice";
  }
  return {
    phase: { ...phase, detail },
    controlLabel: state?.paused ? "Resume" : "Pause",
  };
}

function actionPanelView(state) {
  const status = state?.status ?? {};
  const decision = status.last_decision;
  const isCurrent =
    status.phase === "selected" &&
    decision?.context_id === status.context_id &&
    decision?.probabilities;
  if ((state?.available_actions ?? []).length) {
    return {
      heading: "Available actions",
      note: isCurrent ? "Jev preference" : "Probabilities unavailable",
      rows: state.available_actions.map((action) => ({
        ...action,
        probability: Number.isFinite(isCurrent?.[action.id]) ? isCurrent[action.id] : null,
        chosen: Boolean(isCurrent && decision.action_id === action.id),
      })),
    };
  }
  if (isCurrent) {
    return {
      heading: "Last decision",
      note: decision.source === "deterministic" ? "Deterministic" : "Jev preference",
      rows: Object.entries(isCurrent).map(([id, probability]) => ({
        id,
        label: decision.labels?.[id] ?? id,
        probability,
        chosen: decision.action_id === id,
      })),
    };
  }
  return { heading: "Available actions", note: "Probabilities unavailable", rows: [] };
}

function plannerPanelView(planner) {
  const advice = planner?.advice;
  return {
    hint: advice ? advice.hint : "Luna will advise when Jev gets stuck",
    location: advice?.location ?? "—",
    avoid: advice?.avoid ?? "—",
    success: advice?.success_signal ?? "—",
  };
}

// Token totals are observed subtotals; absent provider usage is never a zero.
function usagePanelView(usage) {
  if (!usage) return { calls: "Unavailable", input: "Unavailable", output: "Unavailable", cached: "Unavailable" };
  function amount(value, missing) {
    if (!Number.isFinite(value)) return "Unavailable";
    return `${value.toLocaleString()}${missing ? ` known · ${missing} unknown` : ""}`;
  }
  return {
    calls: `${usage.calls} calls · ${usage.responses} responses · ${usage.errors} errors · ${usage.stale} stale · ${usage.pending} pending`,
    input: amount(usage.input_tokens, usage.missing_input),
    output: amount(usage.output_tokens, usage.missing_output),
    cached: amount(usage.cached_input_tokens, usage.missing_cached),
  };
}

function coachingPanelView(planner) {
  const budget = planner?.budget;
  const limit = (value) => value == null ? "unlimited" : value.toLocaleString();
  return {
    budget: !budget ? "Coaching budget unavailable" :
      `${budget.exhausted ? "Coaching budget reached" : "Coaching budget available"} · ${budget.calls}/${limit(budget.max_calls)} calls · ${budget.known_tokens.toLocaleString()}/${limit(budget.max_tokens)} known tokens${budget.unknown_usage ? " · some usage unknown" : ""}${budget.reason ? ` · ${budget.reason.replaceAll("_", " ")}` : ""}`,
    interventions: (planner?.interventions ?? []).map((item) => {
      const terminal = ["error", "invalid", "stale", "expired", "superseded", "run_ended"].includes(item.status);
      return {
        hint: `#${item.call} · ${item.hint || (item.status === "pending" ? "Awaiting advice" : "No advice returned")}`,
        detail: [
          `Trigger: ${item.trigger}`, `Status: ${item.status}`,
          item.selected === true ? "Suggested action selected" : item.hint && item.selected === false ? "Suggested action not selected" : "Selection not observed",
          item.action_outcome ? `Action: ${item.action_outcome}` : !terminal && item.selected ? "Action outcome pending" : "No action outcome recorded",
          item.action_reason,
          item.story_progress ? `Story progress observed: ${Object.entries(item.progress_evidence ?? {}).map(([field, change]) => `${field}: ${JSON.stringify(change.before)} → ${JSON.stringify(change.after)}`).join("; ")}` : "No story progress observed yet",
        ].filter(Boolean).join(" · "),
      };
    }),
  };
}

function renderUsageAndCoaching(state) {
  for (const name of ["decision", "planner"]) {
    const usage = usagePanelView(state.usage?.[name]);
    for (const field of ["calls", "input", "output", "cached"]) text(`${name}-${field}`, usage[field]);
  }
  const coaching = coachingPanelView(state.planner);
  text("planner-budget", coaching.budget);
  const container = document.getElementById("interventions");
  container.replaceChildren();
  for (const intervention of [...coaching.interventions].reverse()) {
    const item = document.createElement("li");
    const hint = document.createElement("strong");
    hint.textContent = intervention.hint;
    const detail = document.createElement("span");
    detail.textContent = intervention.detail;
    item.append(hint, detail);
    container.append(item);
  }
  if (!coaching.interventions.length) {
    const empty = document.createElement("li");
    empty.className = "empty";
    empty.textContent = "No coaching interventions yet.";
    container.append(empty);
  }
}

function text(id, value) {
  const element = document.getElementById(id);
  if (element) element.textContent = value;
}

function hpPercent(member) {
  return member.max_hp > 0 ? Math.max(0, Math.min(100, (member.hp / member.max_hp) * 100)) : 0;
}

function renderHp(container, member, role) {
  const row = document.createElement("div");
  row.className = "hp-row";
  const heading = document.createElement("div");
  heading.className = "hp-heading";
  const name = document.createElement("strong");
  name.textContent = `${role === "Party" ? "" : `${role} · `}${member.species} Lv. ${member.level}`;
  const amount = document.createElement("span");
  amount.textContent = `${member.hp}/${member.max_hp} HP${member.status && member.status !== "Healthy" ? ` · ${member.status}` : ""}`;
  heading.append(name, amount);
  const track = document.createElement("div");
  track.className = "hp-track";
  const fill = document.createElement("span");
  fill.style.width = `${hpPercent(member)}%`;
  fill.className = hpPercent(member) <= 25 ? "critical" : hpPercent(member) <= 50 ? "warn" : "";
  track.append(fill);
  row.append(heading, track);
  container.append(row);
}

function renderActions(state) {
  const container = document.getElementById("actions");
  container.replaceChildren();
  const panel = actionPanelView(state);
  text("actions-heading", panel.heading);
  text("probability-note", panel.note);
  if (!panel.rows.length) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "No meaningful choice right now.";
    container.append(empty);
    return;
  }
  const ranked = [...panel.rows].sort((a, b) => (b.probability ?? -1) - (a.probability ?? -1));
  for (const [index, action] of ranked.entries()) {
    const probability = action.probability;
    const item = document.createElement("div");
    item.className = `action-row${action.chosen ? " chosen" : ""}`;
    const line = document.createElement("div");
    line.className = "action-line";
    const label = document.createElement("span");
    label.textContent = action.label;
    const rank = document.createElement("span");
    rank.className = "action-rank";
    rank.textContent = String(index + 1).padStart(2, "0");
    const value = document.createElement("strong");
    value.textContent = Number.isFinite(probability) ? `${(probability * 100).toFixed(1)}%` : "—";
    line.append(rank, label, value);
    const bar = document.createElement("div");
    bar.className = "probability-track";
    const fill = document.createElement("span");
    fill.style.width = Number.isFinite(probability) ? `${probability * 100}%` : "0";
    bar.append(fill);
    item.append(line, bar);
    if (action.chosen) {
      const badge = document.createElement("span");
      badge.className = "choice-badge";
      badge.textContent = "✓ Jev’s choice";
      item.append(badge);
    }
    container.append(item);
  }
}

function renderRecent(choices) {
  const container = document.getElementById("recent");
  container.replaceChildren();
  if (!choices?.length) {
    const empty = document.createElement("li");
    empty.className = "empty";
    empty.textContent = "No choices yet.";
    container.append(empty);
    return;
  }
  for (const choice of [...choices].reverse()) {
    const item = document.createElement("li");
    const action = document.createElement("strong");
    action.textContent = choice.labels?.[choice.action_id] ?? choice.action_id;
    const meta = document.createElement("span");
    const latency = Number.isFinite(choice.latency_ms) ? ` · ${Math.round(choice.latency_ms)} ms` : "";
    meta.textContent = `${choice.source === "model" ? "Jev" : "Automatic"}${latency}`;
    item.append(action, meta);
    container.append(item);
  }
}

function render(state) {
  const view = buildViewModel(state);
  text("connection", "Live");
  document.getElementById("connection").className = "connection live";
  text("phase-label", view.phase.label);
  text("phase-detail", view.phase.detail);
  document.getElementById("phase-dot").className = `phase-dot ${view.phase.tone}`;
  text("mission", state.mission ?? "Earn the Stone Badge in Rustboro");
  text("badge-count", `${state.progress?.stone_badge ? 1 : 0} / 1 badge`);
  text("manual-count", Number.isInteger(state.manual_actions) ? state.manual_actions.toLocaleString() : "—");
  text("run-result", state.progress?.completed ? "Victory verified this run" : state.checkpoint ? "Target already achieved in the loaded checkpoint · no fresh win" : "Journey in progress · no verified win yet");
  text("goal", state.goal ?? "Waiting for the first game observation");
  text("decision-count", Number.isInteger(state.decision_count) ? state.decision_count.toLocaleString() : "—");
  text("option-count", `${(state.available_actions ?? []).length} options`);
  text("planner-count", state.planner?.calls ?? "—");
  text("planner-heading", state.planner?.model?.includes("luna") || !state.planner ? "Luna’s hint" : "Planner’s hint");
  text("planner-meta", "Planner not enabled");
  text("planner-advice", "Jev is choosing without planner guidance.");
  document.getElementById("planner-details").hidden = !state.planner?.advice;
  if (state.planner) {
    const planner = state.planner;
    const panel = plannerPanelView(planner);
    text("planner-meta", `${planner.pending ? "Reviewing the game…" : planner.advice ? "Guidance active" : "On standby"} · ${planner.model}`);
    text("planner-advice", panel.hint);
    text("planner-location", panel.location);
    text("planner-avoid", panel.avoid);
    text("planner-success", panel.success);
    if (planner.pending) text("phase-detail", "Planner is reviewing the game; Jev chooses next");
  }
  const last = state.status?.last_decision;
  text("active-label", state.active_action ? "Current action" : "Last choice");
  text("active-action", state.active_action?.label ?? last?.labels?.[last.action_id] ?? last?.action_id ?? "Waiting for a choice");
  const latency = state.status?.last_decision?.latency_ms;
  text("latency", Number.isFinite(latency) ? `${Math.round(latency)} ms to decide` : "");
  text("game-state", state.observation?.game_state ?? "Waiting for game");
  const position = state.observation?.position;
  text("position", position ? `Map ${position.map_id.join(".")} · ${position.coordinates.join(", ")}` : "—");
  const control = document.getElementById("pause-control");
  control.textContent = view.controlLabel;
  control.disabled = false;
  control.dataset.paused = String(Boolean(state.paused));

  renderActions(state);
  renderUsageAndCoaching(state);

  const party = document.getElementById("party");
  party.replaceChildren();
  for (const member of state.observation?.party ?? []) renderHp(party, member, "Party");
  if (!party.children.length) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "Waiting for Jev’s first Pokémon";
    party.append(empty);
  }
  const opponent = document.getElementById("opponent");
  opponent.replaceChildren();
  if (state.observation?.opponent) renderHp(opponent, state.observation.opponent, "Opponent");

  const journeySteps = [...document.querySelectorAll("#progress [data-step]")];
  for (const item of journeySteps) {
    const achieved = Boolean(state.progress?.[item.dataset.step]);
    item.classList.toggle("done", achieved);
    item.setAttribute("aria-label", `${item.textContent.trim()}: ${achieved ? "achieved" : "not observed"}`);
    item.hidden = state.target === "rival" && ["pokedex", "petalburg", "woods", "gym", "stone_badge"].includes(item.dataset.step);
  }
  const visibleSteps = journeySteps.filter((item) => !item.hidden);
  const completedSteps = visibleSteps.filter((item) => item.classList.contains("done"));
  for (const item of visibleSteps) item.classList.remove("current");
  visibleSteps.find((item) => !item.classList.contains("done"))?.classList.add("current");
  text("journey-count", `${completedSteps.length}/${visibleSteps.length}`);
  renderRecent(state.recent_choices);
}

async function refresh() {
  try {
    const response = await fetch("/custom_state", { cache: "no-store" });
    if (!response.ok) throw new Error(`status ${response.status}`);
    const payload = await response.json();
    const state = payload.jev_emerald;
    if (!state || typeof state !== "object") throw new Error("Jev state is unavailable");
    render(state);
  } catch (error) {
    text("connection", "Reconnecting");
    document.getElementById("connection").className = "connection lost";
    text("phase-label", "Viewer disconnected");
    text("phase-detail", error instanceof Error ? error.message : "Status is unavailable");
    document.getElementById("phase-dot").className = "phase-dot error";
  }
}

async function setPaused(paused) {
  const control = document.getElementById("pause-control");
  control.disabled = true;
  try {
    const response = await fetch("/jev/control", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ paused }),
    });
    if (!response.ok) throw new Error(`control status ${response.status}`);
  } catch (error) {
    text("phase-label", "Control failed");
    text("phase-detail", error instanceof Error ? error.message : "Pause control is unavailable");
    document.getElementById("phase-dot").className = "phase-dot error";
  } finally {
    control.disabled = false;
  }
}

if (typeof document !== "undefined") {
  document.getElementById("pause-control").addEventListener("click", (event) => {
    setPaused(event.currentTarget.dataset.paused !== "true");
  });
  // Wait for each response so a slow server cannot accumulate overlapping polls
  // or render older state after a newer response.
  async function poll() {
    await refresh();
    window.setTimeout(poll, 250);
  }
  poll();
}

if (typeof module !== "undefined") module.exports = { actionPanelView, buildViewModel, plannerPanelView, usagePanelView, coachingPanelView };
