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
  job: (id) => request(`/api/jobs/${id}`),
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
  issueUrls: (id, params) => request(`/api/jobs/${id}/issues/urls${qs(params)}`),
  exportUrl: (id, entity) => `/api/jobs/${id}/export?entity=${entity}`,

  diff: (params) => request(`/api/diff${qs(params)}`),
  diffUrls: (params) => request(`/api/diff/urls${qs(params)}`),
  flapping: (params) => request(`/api/diff/flapping${qs(params)}`),

  segments: (clientId) => request(`/api/clients/${encodeURIComponent(clientId)}/segments`),
  createSegment: (clientId, payload) =>
    request(`/api/clients/${encodeURIComponent(clientId)}/segments`, {
      method: "POST",
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
};
