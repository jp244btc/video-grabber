/* Video Grabber - options page. */

const $ = (id) => document.getElementById(id);
const DEFAULTS = { server: "http://127.0.0.1:5001", token: "" };

function note(text, good) {
  $("note").textContent = text;
  $("note").className = good ? "good" : "bad";
}

function current() {
  return {
    server: ($("server").value.trim() || DEFAULTS.server).replace(/\/+$/, ""),
    token: $("token").value.trim(),
  };
}

chrome.storage.sync.get(DEFAULTS).then((cfg) => {
  $("server").value = cfg.server;
  $("token").value = cfg.token;
});

$("save").addEventListener("click", async () => {
  const cfg = current();
  await chrome.storage.sync.set(cfg);
  $("server").value = cfg.server;
  note("Saved.", true);
});

$("test").addEventListener("click", async () => {
  const cfg = current();
  note("Checking...", true);
  try {
    const res = await fetch(cfg.server + "/api/ping", {
      headers: { "X-VG-Token": cfg.token },
    });
    if (res.status === 403) {
      note("Reached the app, but the token is wrong.", false);
      return;
    }
    if (!res.ok) {
      note(`Reached the app, but it replied ${res.status}.`, false);
      return;
    }
    const data = await res.json();
    // Save on success. Testing without saving used to report "Connected" on a
    // token that was never stored, so the popup still said "no token".
    await chrome.storage.sync.set(cfg);
    note(`Connected and saved. yt-dlp ${data.ytdlp}.`, true);
  } catch (e) {
    note("No answer. Is the downloader running?", false);
  }
});

// Belt and braces: anything typed here is stored as soon as the field loses
// focus, so there is no way to leave this page with an unsaved token.
for (const id of ["server", "token"]) {
  $(id).addEventListener("change", async () => {
    await chrome.storage.sync.set(current());
    note("Saved.", true);
  });
}
