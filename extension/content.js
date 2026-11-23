const BUTTON_ID = "vnt-take-notes";

function currentVideoId() {
  return location.pathname === "/watch" ? new URLSearchParams(location.search).get("v") : null;
}

function showToast(text, isError = false) {
  document.getElementById("vnt-toast")?.remove();
  const toast = document.createElement("div");
  toast.id = "vnt-toast";
  toast.className = isError ? "vnt-toast vnt-error" : "vnt-toast";
  toast.textContent = text;
  document.body.appendChild(toast);
  setTimeout(() => toast.remove(), 5000);
}

const DIALOG_ID = "vnt-dialog";
const MAX_PROMPT = 1000;
const EXAMPLES = ["Focus on formulas and definitions", "Write the notes in Hindi",
                  "Explain it for a complete beginner", "Keep it short: 3 bullets per section"];

// Ask for optional instructions. Resolves to the text, "" for the default notes, or null if cancelled.
function askForPrompt() {
  return new Promise((resolve) => {
    const overlay = document.createElement("div");
    overlay.id = DIALOG_ID;
    overlay.className = "vnt-overlay";
    overlay.innerHTML = `
      <div class="vnt-modal" role="dialog" aria-modal="true" aria-labelledby="vnt-dialog-title">
        <h2 id="vnt-dialog-title">Take notes</h2>
        <p class="vnt-hint">What do you want from these notes(optional)</p>
        <textarea class="vnt-input" rows="4" maxlength="${MAX_PROMPT}"
                  placeholder="e.g. Focus on formulas and definitions"></textarea>
        <div class="vnt-chips"></div>
        <div class="vnt-row">
          <span class="vnt-count"></span>
          <button type="button" class="vnt-btn vnt-cancel">Cancel</button>
          <button type="button" class="vnt-btn vnt-primary vnt-go">Take notes</button>
        </div>
      </div>`;
    const input = overlay.querySelector(".vnt-input");
    const count = overlay.querySelector(".vnt-count");
    const updateCount = () => { count.textContent = `${input.value.length} / ${MAX_PROMPT}`; };

    const chips = overlay.querySelector(".vnt-chips");
    const addChip = (text, label = text) => {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = "vnt-chip";
      chip.textContent = label;
      chip.title = text;
      chip.addEventListener("click", () => { input.value = text; updateCount(); input.focus(); });
      chips.appendChild(chip);
    };
    EXAMPLES.forEach((e) => addChip(e));
    chrome.storage.local.get("lastPrompt").then(({ lastPrompt }) => {
      if (lastPrompt) addChip(lastPrompt, "↺ Last used");
    }).catch(() => {});

    const close = (value) => {
      overlay.remove();
      if (value) chrome.storage.local.set({ lastPrompt: value }).catch(() => {});
      resolve(value);
    };
    overlay.querySelector(".vnt-cancel").addEventListener("click", () => close(null));
    overlay.querySelector(".vnt-go").addEventListener("click", () => close(input.value.trim()));
    overlay.addEventListener("click", (e) => { if (e.target === overlay) close(null); });
    input.addEventListener("input", updateCount);
    // Keep YouTube's keyboard shortcuts (k, f, space...) from firing while typing.
    for (const type of ["keydown", "keyup", "keypress"]) {
      overlay.addEventListener(type, (e) => {
        e.stopPropagation();
        if (type !== "keydown") return;
        if (e.key === "Escape") close(null);
        if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) close(input.value.trim());
      });
    }

    updateCount();
    (document.fullscreenElement || document.body).appendChild(overlay);   // visible in fullscreen too
    input.focus();
  });
}

async function onClick(button) {
  const videoId = currentVideoId();          // read at click time: always the video on screen now
  if (!videoId) return;
  if (document.getElementById(DIALOG_ID)) return;     // already asking
  const prompt = await askForPrompt();
  if (prompt === null) return;               // cancelled: nothing is sent
  button.disabled = true;
  showToast(prompt ? "Sending with your instructions…" : "Sending to Video Note Taker…");
  const title = document.title.replace(/ - YouTube$/, "");
  try {
    const reply = await chrome.runtime.sendMessage({ type: "takeNotes", videoId, title, prompt });
    if (reply?.ok) showToast("Notes queued! Click the Video Note Taker icon to follow progress.");
    else showToast(reply?.message || "Something went wrong.", true);
  } catch {
    showToast("The extension was reloaded. Refresh this page and try again.", true);
  } finally {
    setTimeout(() => { button.disabled = false; }, 3000);
  }
}

function ensureButton() {
  if (!currentVideoId()) return;
  const controls = document.querySelector(".ytp-right-controls");
  if (!controls || controls.querySelector(`#${BUTTON_ID}`)) return;
  const button = document.createElement("button");
  button.id = BUTTON_ID;
  button.className = "ytp-button vnt-button";
  button.title = "Take Notes";
  button.textContent = "Take Notes";
  button.addEventListener("click", () => onClick(button));
  controls.prepend(button);
}

// YouTube changes pages without reloading, so keep making sure the button is there.
document.addEventListener("yt-navigate-finish", ensureButton);
setInterval(ensureButton, 1000);
ensureButton();
