/** Cliente mínimo de la API del crawler (misma origin). */

async function request(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (res.status === 204) return null;
  const body = await res.json().catch(() => null);
  if (!res.ok) {
    const detail = body && body.detail ? body.detail : res.statusText;
    const err = new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    err.status = res.status;
    throw err;
  }
  return body;
}

const qs = (params) => {
  const clean = Object.entries(params || {}).filter(
    ([, v]) => v !== null && v !== undefined && v !== "",
  );
  return clean.length ? "?" + new URLSearchParams(clean).toString() : "";
};

export const api = {
  jobs: (params) => request(`/api/jobs${qs(params)}`),
  createJob: (payload) =>
    request("/api/jobs", { method: "POST", body: JSON.stringify(payload) }),
  cancelJob: (id) => request(`/api/jobs/${id}/cancel`, { method: "PATCH" }),
  deleteJob: (id) => request(`/api/jobs/${id}`, { method: "DELETE" }),
  reanalyze: (id, payload) =>
    request(`/api/jobs/${id}/reanalyze`, {
      method: "POST",
      body: JSON.stringify(payload || {}),
    }),
  progress: (id) => request(`/api/jobs/${id}/progress`),
  stats: (id, params) => request(`/api/jobs/${id}/stats${qs(params)}`),
  urls: (id, params) => request(`/api/jobs/${id}/urls${qs(params)}`),
  urlDetail: (id, urlId) => request(`/api/jobs/${id}/urls/${urlId}`),
  issues: (id, params) => request(`/api/jobs/${id}/issues${qs(params)}`),
  exportUrl: (id, entity) => `/api/jobs/${id}/export?entity=${entity}`,

  links: (id, params) => request(`/api/jobs/${id}/links${qs(params)}`),
  insights: (id) => request(`/api/jobs/${id}/insights`),
  resumeJob: (id) => request(`/api/jobs/${id}/resume`, { method: "POST" }),
  backupUrl: (id) => `/api/jobs/${id}/backup`,
  importJob: (file, params) => {
    const fd = new FormData();
    fd.append("file", file);
    return fetch(`/api/jobs/import${qs(params)}`, { method: "POST", body: fd })
      .then(async (r) => {
        const body = await r.json().catch(() => null);
        if (!r.ok) throw new Error(body && body.detail ? JSON.stringify(body.detail) : r.statusText);
        return body;
      });
  },

  // T5/T9/T10/T18/T21 — mejoras nuevas
  freshness: (id, params) => request(`/api/jobs/${id}/freshness${qs(params)}`),
  strikingDistance: (id, params) => request(`/api/jobs/${id}/striking-distance${qs(params)}`),
  linkSuggestions: (id, params) => request(`/api/jobs/${id}/link-suggestions${qs(params)}`),
  decideSuggestion: (sid, payload) =>
    request(`/api/link-suggestions/${sid}/decision`, {
      method: "POST", body: JSON.stringify(payload),
    }),
  reviewIssue: (iid, payload) =>
    request(`/api/issues/${iid}/review`, {
      method: "POST", body: JSON.stringify(payload),
    }),
  proposals: (id, params) => request(`/api/jobs/${id}/proposals${qs(params)}`),
  proposalsExportUrl: (id, params) => `/api/jobs/${id}/proposals/export${qs(params)}`,
  linkTargets: (id, params) => request(`/api/jobs/${id}/link-targets${qs(params)}`),
  bulkDecision: (id, payload) =>
    request(`/api/jobs/${id}/proposals/bulk-decision`, {
      method: "POST", body: JSON.stringify(payload),
    }),
  pagerankDelta: (id, params) => request(`/api/jobs/${id}/pagerank-delta${qs(params)}`),
  sectionFlows: (id) => request(`/api/jobs/${id}/section-flows`),
  archEdges: (id, params) => request(`/api/jobs/${id}/arch-edges${qs(params)}`),
  simulate: (id, payload) =>
    request(`/api/jobs/${id}/pagerank-simulate`, {
      method: "POST", body: JSON.stringify(payload),
    }),

  // Semántica (paridad con legacy)
  gscAccounts: () => request("/api/semantic/gsc-accounts"),
  addGscAccount: (payload) =>
    request("/api/semantic/gsc-accounts", { method: "POST", body: JSON.stringify(payload) }),
  deleteGscAccount: (id) =>
    request(`/api/semantic/gsc-accounts/${id}`, { method: "DELETE" }),
  gscProperties: (id) => request(`/api/semantic/gsc-accounts/${id}/properties`),
  geminiAccounts: () => request("/api/semantic/gemini-accounts"),
  addGeminiAccount: (payload) =>
    request("/api/semantic/gemini-accounts", { method: "POST", body: JSON.stringify(payload) }),
  deleteGeminiAccount: (id) =>
    request(`/api/semantic/gemini-accounts/${id}`, { method: "DELETE" }),

  fetchGsc: (id, payload) =>
    request(`/api/jobs/${id}/semantic/fetch-gsc`, { method: "POST", body: JSON.stringify(payload) }),
  semanticAnalyze: (id, payload) =>
    request(`/api/jobs/${id}/semantic/analyze`, { method: "POST", body: JSON.stringify(payload) }),
  semanticStatus: (id) => request(`/api/jobs/${id}/semantic/status`),
  semanticResults: (id) => request(`/api/jobs/${id}/semantic/results`),
  semanticCannibalization: (id, params) =>
    request(`/api/jobs/${id}/semantic/cannibalization${qs(params)}`),
  semanticDrift: (id) => request(`/api/jobs/${id}/semantic/drift`),
  semanticGap: (id, payload) =>
    request(`/api/jobs/${id}/semantic/gap-analysis`, { method: "POST", body: JSON.stringify(payload) }),
  targetRings: (id, payload) =>
    request(`/api/jobs/${id}/semantic/target-rings`, { method: "POST", body: JSON.stringify(payload) }),
  anchorRelevance: (id, payload) =>
    request(`/api/jobs/${id}/semantic/anchor-relevance`, { method: "POST", body: JSON.stringify(payload) }),
  queryCoverage: (id) => request(`/api/jobs/${id}/semantic/query-coverage`),
  runQueryCoverage: (id, payload) =>
    request(`/api/jobs/${id}/semantic/query-coverage`, { method: "POST", body: JSON.stringify(payload) }),
  semanticExportUrl: (id) => `/api/jobs/${id}/semantic/export`,

  diff: (params) => request(`/api/diff${qs(params)}`),
  diffUrls: (params) => request(`/api/diff/urls${qs(params)}`),
  flapping: (params) => request(`/api/diff/flapping${qs(params)}`),

  performanceTimeline: (clientId, params) =>
    request(`/api/clients/${encodeURIComponent(clientId)}/timeline${qs(params)}`),
  performanceSummary: (clientId, params) =>
    request(`/api/clients/${encodeURIComponent(clientId)}/performance-summary${qs(params)}`),
  watchlistTimeline: (clientId) =>
    request(`/api/clients/${encodeURIComponent(clientId)}/watchlist-timeline`),

  segments: (clientId) => request(`/api/clients/${encodeURIComponent(clientId)}/segments`),
  createSegment: (clientId, payload) =>
    request(`/api/clients/${encodeURIComponent(clientId)}/segments`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  updateSegment: (clientId, segId, payload) =>
    request(`/api/clients/${encodeURIComponent(clientId)}/segments/${segId}`, {
      method: "PUT",
      body: JSON.stringify(payload),
    }),
  deleteSegment: (clientId, segId) =>
    request(`/api/clients/${encodeURIComponent(clientId)}/segments/${segId}`, {
      method: "DELETE",
    }),
  previewSegments: (clientId, rules) =>
    request(`/api/clients/${encodeURIComponent(clientId)}/segments/preview`, {
      method: "POST",
      body: JSON.stringify({ rules }),
    }),

  watchlist: (clientId) => request(`/api/clients/${encodeURIComponent(clientId)}/watchlist`),
  addWatch: (clientId, payload) =>
    request(`/api/clients/${encodeURIComponent(clientId)}/watchlist`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  deleteWatch: (clientId, entryId) =>
    request(`/api/clients/${encodeURIComponent(clientId)}/watchlist/${entryId}`, {
      method: "DELETE",
    }),
  suggestedThresholds: (clientId) =>
    request(`/api/clients/${encodeURIComponent(clientId)}/suggested-thresholds`),

  extractionSchema: (clientId) =>
    request(`/api/clients/${encodeURIComponent(clientId)}/extraction-schema`),
  saveExtractionSchemaForm: (clientId, form) =>
    request(`/api/clients/${encodeURIComponent(clientId)}/extraction-schema`, {
      method: "PUT",
      body: JSON.stringify({ form }),
    }),

  clientSettings: (clientId) =>
    request(`/api/clients/${encodeURIComponent(clientId)}/settings`),
  saveClientSettings: (clientId, payload) =>
    request(`/api/clients/${encodeURIComponent(clientId)}/settings`, {
      method: "PUT",
      body: JSON.stringify(payload),
    }),

  entityCatalog: (clientId, params) =>
    request(`/api/clients/${encodeURIComponent(clientId)}/entity-catalog${qs(params)}`),
  addCatalogEntry: (clientId, payload) =>
    request(`/api/clients/${encodeURIComponent(clientId)}/entity-catalog`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  deleteCatalogEntry: (clientId, entityId) =>
    request(`/api/clients/${encodeURIComponent(clientId)}/entity-catalog/${encodeURIComponent(entityId)}`, {
      method: "DELETE",
    }),

  entitiesStatus: (jobId) => request(`/api/jobs/${jobId}/entities/status`),
};
