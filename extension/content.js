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

async function onClick(button) {
  const videoId = currentVideoId();          // read at click time: always the video on screen now
  if (!videoId) return;
  button.disabled = true;
  showToast("Sending to Video Note Taker…");
  const title = document.title.replace(/ - YouTube$/, "");
  try {
    const reply = await chrome.runtime.sendMessage({ type: "takeNotes", videoId, title });
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
