const GATEWAY = "http://localhost:8000";
const TERMINAL = new Set(["COMPLETED", "COMPLETED_WITH_WARNINGS", "FAILED", "DEAD_LETTERED"]);
const MAX_JOBS = 20;

class ApiError extends Error {
  constructor(code, message) {
    super(message);
    this.code = code;
  }
}

async function api(method, path, body) {
  let res;
  try {
    res = await fetch(GATEWAY + path, {
      method,
      headers: body ? { "Content-Type": "application/json" } : {},
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new ApiError("SERVER_UNREACHABLE", "Can't reach the note-taking server. Is it running?");
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new ApiError(data.error?.code || `HTTP_${res.status}`,
                       data.error?.message || `Server error (${res.status})`);
  }
  return data;
}

// ---- Notion: one-time OAuth; the token is kept in extension storage ----

async function getNotion() {
  const { notion } = await chrome.storage.local.get("notion");
  return notion || null;
}

async function connectNotion() {
  const { client_id } = await api("GET", "/oauth/notion/config");
  const redirectUri = chrome.identity.getRedirectURL();
  const state = crypto.randomUUID();
  const authUrl = "https://api.notion.com/v1/oauth/authorize?" + new URLSearchParams({
    client_id, response_type: "code", owner: "user", redirect_uri: redirectUri, state,
  });
  let result;
  try {
    result = await chrome.identity.launchWebAuthFlow({ url: authUrl, interactive: true });
  } catch {
    throw new ApiError("NOTION_CANCELLED", "Notion connection was cancelled.");
  }
  const params = new URL(result).searchParams;
  if (params.get("state") !== state || !params.get("code")) {
    throw new ApiError("NOTION_CANCELLED", "Notion connection was cancelled.");
  }
  const tokens = await api("POST", "/oauth/notion/exchange",
                           { code: params.get("code"), redirect_uri: redirectUri });
  const notion = { token: tokens.access_token, workspace: tokens.workspace_name || "your workspace" };
  await chrome.storage.local.set({ notion });
  return { workspace: notion.workspace };
}

// ---- Jobs: submitted here, polled here, read by the popup ----

async function getJobs() {
  const { jobs = [] } = await chrome.storage.local.get("jobs");
  return jobs;
}

async function saveJobs(jobs) {
  await chrome.storage.local.set({ jobs: jobs.slice(0, MAX_JOBS) });
  const active = jobs.filter((j) => !TERMINAL.has(j.status)).length;
  await chrome.action.setBadgeText({ text: active ? String(active) : "" });
}

async function takeNotes({ videoId, title }) {
  const notion = (await getNotion()) || (await connectNotion(), await getNotion());
  const job = await api("POST", "/jobs", {
    youtube_url: `https://www.youtube.com/watch?v=${videoId}`,
    notion_token: notion.token,
  });
  const jobs = await getJobs();
  jobs.unshift({ job_id: job.job_id, video_id: videoId, title, status: job.status,
                 progress: 0, submitted_at: Date.now() });
  await saveJobs(jobs);
  return { job_id: job.job_id };
}

async function refreshJobs() {
  const updates = {};
  for (const job of await getJobs()) {
    if (TERMINAL.has(job.status)) continue;
    try {
      const s = await api("GET", `/jobs/${job.job_id}`);
      updates[job.job_id] = {
        stage: TERMINAL.has(s.status) ? job.status : s.status,
        status: s.status, progress: s.progress, title: s.video_title || job.title,
        retry_at: s.retry_at, last_error: s.last_error, error: s.error,
        warnings: s.warnings, result: s.result, unreachable: false,
      };

    } catch (e) {
      updates[job.job_id] = e.code === "JOB_NOT_FOUND"
        ? { stage: job.status, status: "FAILED", error: { code: e.code, message: "The server no longer knows this job." } }
        : { unreachable: true };
    }
  }
  const jobs = await getJobs();          // re-read: a new job may have been added meanwhile
  for (const job of jobs) Object.assign(job, updates[job.job_id] || {});
  await saveJobs(jobs);
  return jobs;
}

// ---- Messages from the content script and the popup ----

const HANDLERS = {
  takeNotes,
  connect: connectNotion,
  disconnect: async () => { await chrome.storage.local.remove("notion"); },
  getState: async () => {
    const notion = await getNotion();
    return { connected: !!notion, workspace: notion?.workspace, jobs: await refreshJobs() };
  },
  clearFinished: async () => {
    await saveJobs((await getJobs()).filter((j) => !TERMINAL.has(j.status)));
  },
};

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  const handler = HANDLERS[msg.type];
  if (!handler) return false;
  handler(msg).then(
    (result) => sendResponse({ ok: true, ...result }),
    (e) => sendResponse({ ok: false, code: e.code || "ERROR", message: e.message }),
  );
  return true;   // keep the message channel open for the async reply
});

// Keep job progress fresh even while the popup is closed (also updates the badge).
const startPolling = () => chrome.alarms.create("poll", { periodInMinutes: 0.5 });
chrome.runtime.onInstalled.addListener(startPolling);
chrome.runtime.onStartup.addListener(startPolling);
chrome.alarms.onAlarm.addListener((alarm) => { if (alarm.name === "poll") refreshJobs(); });
