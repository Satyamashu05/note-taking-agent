const STEPS = [
  ["QUEUED", "Queued"], ["TRANSCRIBING", "Transcribing"], ["SUMMARIZING", "Summarizing"],
  ["EXTRACTING_FRAMES", "Extracting frames"], ["PUBLISHING", "Publishing"], ["DONE", "Done"],
];
const TERMINAL = new Set(["COMPLETED", "COMPLETED_WITH_WARNINGS", "FAILED", "DEAD_LETTERED"]);

// Why a job is waiting (the status stays on its stage while a retry is scheduled).
const WAIT_REASONS = {
  LLM_RATE_LIMITED: "the AI service's rate limit was reached",
  LLM_UNAVAILABLE: "the AI service is busy",
  NOTION_RATE_LIMITED: "Notion's rate limit was reached",
  NOTION_UNAVAILABLE: "Notion is busy",
  YOUTUBE_RATE_LIMITED: "YouTube is limiting requests",
  VIDEO_UNAVAILABLE: "the video couldn't be opened",
  RATE_LIMITED: "a rate limit was reached",
};
// Failures that need more guidance than the server's own message.
const FAILURE_HELP = {
  NOTION_UNAUTHORIZED: "Notion access expired or was removed. Reconnect Notion, then try again.",
  NOTION_NO_ACCESS: "The extension can't access the Notion page it needs. Reconnect Notion and allow access.",
  VIDEO_UNAVAILABLE: "The video is private, deleted or unavailable.",
  LLM_AUTH_FAILED: "The server's Gemini API key was rejected.",
  LLM_MODEL_NOT_FOUND: "The server's Gemini model name is wrong.",
};
const WARNING_TEXT = {
  FRAME_EXTRACTION_FAILED: "couldn't be captured",
  FRAME_NOT_RELEVANT: "didn't show anything useful",
};

const $ = (selector) => document.querySelector(selector);

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === "class") node.className = value;
    else if (key === "onclick") node.addEventListener("click", value);
    else node.setAttribute(key, value);
  }
  node.append(...children.filter((c) => c != null && c !== false));
  return node;
}

const mmss = (sec) => `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, "0")}`;
const isDone = (job) => job.status === "COMPLETED" || job.status === "COMPLETED_WITH_WARNINGS";
const isFailed = (job) => job.status === "FAILED" || job.status === "DEAD_LETTERED";
const secondsUntilRetry = (job) =>
  !TERMINAL.has(job.status) && job.retry_at ? job.retry_at - Math.floor(Date.now() / 1000) : 0;
const failureText = (error) => FAILURE_HELP[error?.code] || error?.message || "Unknown error.";

function stepIndex(job) {
  if (isDone(job)) return STEPS.length - 1;
  const stage = TERMINAL.has(job.status) ? job.stage : job.status;
  return Math.max(0, STEPS.findIndex(([key]) => key === stage));
}

function describe(job) {
  if (job.unreachable) return { kind: "warn", text: "Can't reach the server. Still trying…" };
  if (job.status === "COMPLETED") return { kind: "ok", text: "Done: your notes are in Notion." };
  if (job.status === "COMPLETED_WITH_WARNINGS") {
    const r = job.result || {};
    return { kind: "warn", text: `Done with warnings: ${r.frames_published} of ${r.frames_requested} images added.` };
  }
  if (job.status === "FAILED") return { kind: "error", text: `Failed: ${failureText(job.error)}` };
  if (job.status === "DEAD_LETTERED") {
    return { kind: "error", text: `Gave up after several attempts: ${failureText(job.error)}` };
  }
  const label = STEPS[stepIndex(job)][1];
  const wait = secondsUntilRetry(job);
  if (wait > 0) {
    const reason = WAIT_REASONS[job.last_error?.code] || "of a temporary problem";
    return { kind: "wait", text: `${label}: waiting because ${reason}. Retrying in ${wait}s.` };
  }
  return { kind: "active", text: `${label}… ${job.progress ?? 0}%` };
}

function renderJob(job) {
  const current = stepIndex(job);
  const info = describe(job);
  const steps = STEPS.map(([, label], i) => {
    let state = "todo";
    if (i < current || isDone(job)) state = "done";
    else if (i === current) state = isFailed(job) ? "failed" : secondsUntilRetry(job) > 0 ? "waiting" : "active";
    return el("li", { class: state }, label);
  });
  const width = isDone(job) || isFailed(job) ? 100 : job.progress || 0;

  const card = el("li", { class: `job ${info.kind}` },
    el("a", { class: "title", href: `https://www.youtube.com/watch?v=${job.video_id}`, target: "_blank" },
       job.title || job.video_id),
    el("ol", { class: "steps" }, ...steps),
    el("div", { class: "progress" }, el("div", { class: "bar", style: `width:${width}%` })),
    el("p", { class: "message" }, info.text));

  if (isDone(job) && job.warnings?.length) {
    card.append(el("ul", { class: "warnings" }, ...job.warnings.map((w) =>
      el("li", {}, `Image at ${mmss(w.timestamp_sec)} ${WARNING_TEXT[w.code] || w.code}.`))));
  }
  if (job.result?.notion_page_url) {
    card.append(el("a", { class: "button primary", href: job.result.notion_page_url, target: "_blank" },
                   "View in Notion"));
  }
  if (isFailed(job) && ["NOTION_UNAUTHORIZED", "NOTION_NO_ACCESS"].includes(job.error?.code)) {
    card.append(el("button", { class: "button", onclick: connect }, "Reconnect Notion"));
  }
  return card;
}

function renderNotion(state) {
  $("#notion").replaceChildren(state.connected
    ? el("span", {}, "Notion: ", el("strong", {}, state.workspace), " · ",
         el("button", { class: "link", onclick: disconnect }, "Disconnect"))
    : el("span", {}, "Notion isn't connected. ",
         el("button", { class: "button primary", onclick: connect }, "Connect Notion")));
}

function showBanner(text) {
  $("#banner").textContent = text || "";
  $("#banner").hidden = !text;
}

async function send(type) {
  try {
    return await chrome.runtime.sendMessage({ type });
  } catch (e) {
    return { ok: false, message: e.message };
  }
}

let busy = false;
async function refresh() {
  if (busy) return;
  busy = true;
  try {
    const state = await send("getState");
    if (!state?.ok) return showBanner(state?.message || "Something went wrong.");
    showBanner("");
    renderNotion(state);
    const jobs = state.jobs || [];
    $("#jobs").replaceChildren(...jobs.map(renderJob));
    $("#empty").hidden = jobs.length > 0;
    $("#clear").hidden = !jobs.some((j) => TERMINAL.has(j.status));
  } finally {
    busy = false;
  }
}

async function connect() {
  const reply = await send("connect");
  if (!reply?.ok) showBanner(reply?.message);
  refresh();
}

async function disconnect() {
  await send("disconnect");
  refresh();
}

$("#clear").addEventListener("click", async () => { await send("clearFinished"); refresh(); });
refresh();
setInterval(refresh, 2000);
