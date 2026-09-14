/*
 * Video Grabber - popup.
 *
 * One card per video detected on the page, Video DownloadHelper style: a frame
 * from the stream, its length, the resolutions on offer, and a Download
 * button. The thumbnail and the rest come from the local app, which runs
 * ffmpeg over the stream - same job VDH's companion app does.
 *
 * Below the cards, "Grab page" hands the page URL to yt-dlp, which knows the
 * site and picks the best rendition itself. Better for a single-video page;
 * useless on a timeline, which is what the cards are for.
 */

const $ = (id) => document.getElementById(id);
const DEFAULTS = { server: "http://127.0.0.1:5001", token: "" };

let cfg = DEFAULTS;
let tab = null;
let pageName = "video";
const watching = new Map();       // job_id -> card elements
let pollTimer = null;

function say(text, good) {
  const box = $("msg");
  box.textContent = text;
  box.className = "show " + (good ? "good" : "bad");
}

function setStatus(state, text) {
  $("dot").className = "dot " + state;
  $("status").textContent = text;
}

async function api(path, body) {
  const res = await fetch(cfg.server.replace(/\/+$/, "") + path, {
    method: body ? "POST" : "GET",
    headers: { "Content-Type": "application/json", "X-VG-Token": cfg.token },
    body: body ? JSON.stringify(body) : undefined,
  });
  let data = {};
  try { data = await res.json(); } catch (e) { /* non-JSON error page */ }
  if (!res.ok) throw new Error(data.error || `Server said ${res.status}.`);
  return data;
}

// "Home / X" -> "Home X", the way VDH names things.
function cleanTitle(t) {
  return (t || "video").replace(/[\/\\:*?"<>|]+/g, " ").replace(/\s+/g, " ").trim().slice(0, 60);
}

function sourceBadge(url) {
  try {
    const h = new URL(url).host.replace(/^www\./, "");
    if (/twimg|twitter|x\.com/.test(h)) return "X";
    if (/youtube|googlevideo|ytimg/.test(h)) return "YT";
    if (/vimeo/.test(h)) return "Vimeo";
    if (/fbcdn|facebook/.test(h)) return "FB";
    if (/tiktok/.test(h)) return "TikTok";
    return h.split(".").slice(-2, -1)[0] || h;
  } catch (e) { return ""; }
}

async function init() {
  cfg = Object.assign({}, DEFAULTS, await chrome.storage.sync.get(DEFAULTS));

  const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
  tab = tabs[0];
  if (tab) {
    pageName = cleanTitle(tab.title);
    $("page-title").textContent = tab.title || tab.url || "";
  }

  let connected = false;
  if (!cfg.token) {
    setStatus("bad", "no token");
    say("Open the full app once and it pairs itself, or paste the token in Options.", false);
  } else {
    try {
      const d = await api("/api/ping");
      setStatus("ok", "yt-dlp " + d.ytdlp);
      connected = true;
    } catch (e) {
      setStatus("bad", /token|refused/i.test(e.message) ? "bad token" : "not running");
      say(
        /token|refused/i.test(e.message)
          ? "The token does not match. Open the full app once to re-pair."
          : "Cannot reach the downloader. Start it with run.bat, then reopen this popup.",
        false
      );
    }
  }

  await renderCards(connected);
}

// Cards are appended, never rebuilt, so a card mid-download keeps its state
// when a new video turns up while the popup is open.
const rendered = new Set();
let cardCount = 0;
let connectedState = false;

async function renderCards(connected) {
  connectedState = connected;
  rendered.clear();
  cardCount = 0;
  $("cards").textContent = "";
  await appendNewCards();
}

async function appendNewCards() {
  if (!tab) return;
  const items = await chrome.runtime.sendMessage({ type: "getFinds", tabId: tab.id });
  const box = $("cards");
  const empty = box.querySelector(".empty");

  // Oldest first so numbering matches the order they appeared on the page.
  for (const item of items.slice().reverse()) {
    const key = item.key || item.url;
    if (rendered.has(key)) continue;
    rendered.add(key);
    cardCount += 1;
    if (empty) empty.remove();
    box.appendChild(buildCard(item, cardCount, connectedState));
  }

  if (!rendered.size && !box.querySelector(".empty")) {
    const note = document.createElement("div");
    note.className = "empty";
    note.textContent = "No video detected yet. Start one playing, then reopen this popup.";
    box.appendChild(note);
  }
}

// New finds while the popup is open show up as they arrive, the way VDH's
// panel fills in while a page keeps loading video.
chrome.storage.onChanged.addListener((changes, area) => {
  if (area !== "session" || !tab) return;
  if (changes[`tab_${tab.id}`]) appendNewCards();
});

function buildCard(item, n, connected) {
  const card = document.createElement("div");
  card.className = "card";

  // --- thumbnail -------------------------------------------------------
  const thumb = document.createElement("div");
  thumb.className = "thumb";
  const spin = document.createElement("div");
  spin.className = "spin";
  thumb.appendChild(spin);
  const src = document.createElement("span");
  src.className = "src";
  src.textContent = sourceBadge(item.url);
  thumb.appendChild(src);

  // --- body ------------------------------------------------------------
  const body = document.createElement("div");
  body.className = "body";

  const titleRow = document.createElement("div");
  titleRow.className = "title-row";
  const kind = document.createElement("span");
  kind.className = "kind";
  kind.textContent = /HLS/i.test(item.kind) ? "HLS" : /DASH/i.test(item.kind) ? "DASH" : (item.kind || "MEDIA").slice(0, 5);
  const name = document.createElement("div");
  name.className = "name";
  const fileBase = `${n} ${pageName}`;
  name.textContent = fileBase + ".mp4";
  name.title = item.url;
  const close = document.createElement("button");
  close.className = "close";
  close.textContent = "×";
  close.title = "Hide this one";
  close.addEventListener("click", () => card.remove());
  titleRow.append(kind, name, close);

  const act = document.createElement("div");
  act.className = "act";
  const sel = document.createElement("select");
  sel.innerHTML = '<option value="best">MP4  best</option><option value="audio">MP3  audio</option>';
  const dl = document.createElement("button");
  dl.className = "dl";
  dl.textContent = "⬇ Download";
  dl.disabled = !connected;
  act.append(sel, dl);

  const prog = document.createElement("div");
  prog.className = "prog";
  prog.hidden = true;
  const fill = document.createElement("i");
  prog.appendChild(fill);

  body.append(titleRow, act, prog);
  card.append(thumb, body);

  // --- fetch preview details -------------------------------------------
  if (connected) {
    api("/api/inspect", { url: item.url, referer: tab.url }).then((d) => {
      spin.remove();
      if (d.thumbnail) {
        const img = document.createElement("img");
        img.src = d.thumbnail;
        img.alt = "";
        thumb.prepend(img);
      }
      if (d.duration_text) {
        const dur = document.createElement("span");
        dur.className = "dur";
        dur.textContent = d.duration_text;
        thumb.appendChild(dur);
      }
      if (d.heights && d.heights.length) {
        sel.innerHTML = "";
        for (const h of d.heights) {
          const o = document.createElement("option");
          o.value = "h:" + h;
          o.textContent = `MP4  ${h}p`;
          sel.appendChild(o);
        }
        const a = document.createElement("option");
        a.value = "audio";
        a.textContent = "MP3  audio";
        sel.appendChild(a);
      }
    }).catch(() => { spin.remove(); });
  } else {
    spin.remove();
  }

  // --- download --------------------------------------------------------
  dl.addEventListener("click", async () => {
    dl.disabled = true;
    dl.textContent = "Sending…";
    const audio = sel.value === "audio";
    try {
      const r = await api("/api/download", {
        url: item.url,
        referer: tab.url,
        mode: audio ? "audio" : "video",
        quality: audio ? "best" : sel.value,
        audio_format: "mp3",
        cookies: "none",
        filename_hint: fileBase,
        title: fileBase,
      });
      name.textContent = fileBase + (audio ? ".mp3" : ".mp4");
      prog.hidden = false;
      watching.set(r.job_id, { dl, fill, prog });
      dl.textContent = "Queued";
      startPolling();
    } catch (e) {
      dl.disabled = false;
      dl.textContent = "⬇ Download";
      say(e.message, false);
    }
  });

  return card;
}

// Progress for cards that have been sent, while the popup stays open.
function startPolling() {
  if (pollTimer) return;
  pollTimer = setInterval(async () => {
    if (!watching.size) { clearInterval(pollTimer); pollTimer = null; return; }
    let jobs;
    try { jobs = await api("/api/jobs"); } catch (e) { return; }
    for (const j of jobs) {
      const w = watching.get(j.id);
      if (!w) continue;
      if (j.status === "downloading") {
        w.fill.style.width = (j.percent || 0) + "%";
        w.dl.textContent = (j.percent || 0).toFixed(0) + "%";
      } else if (j.status === "processing") {
        w.fill.style.width = "100%";
        w.dl.textContent = "Converting…";
      } else if (j.status === "done") {
        w.fill.style.width = "100%";
        w.dl.className = "dl done";
        w.dl.textContent = "✓ Saved";
        w.dl.disabled = false;
        w.dl.onclick = () => api("/api/reveal", { job_id: j.id }).catch((e) => say(e.message, false));
        watching.delete(j.id);
      } else if (j.status === "error" || j.status === "cancelled") {
        w.dl.className = "dl err";
        w.dl.textContent = "Failed";
        w.dl.title = j.message || "";
        say(j.message || "Download failed.", false);
        watching.delete(j.id);
      }
    }
  }, 800);
}

$("grab-page").addEventListener("click", async (e) => {
  if (!tab) return;
  const b = e.target;
  b.disabled = true;
  b.textContent = "Sending…";
  try {
    await api("/api/download", { url: tab.url, mode: "video", quality: "best", cookies: "none", referer: "" });
    b.textContent = "Sent";
    say("Handed to yt-dlp. Progress is in the full app.", true);
  } catch (err) {
    b.disabled = false;
    b.textContent = "Grab page";
    say(err.message, false);
  }
});

$("clear").addEventListener("click", async () => {
  if (!tab) return;
  await chrome.runtime.sendMessage({ type: "clearFinds", tabId: tab.id });
  await renderCards(true);
});

$("open-app").addEventListener("click", () => chrome.tabs.create({ url: cfg.server }));
$("open-options").addEventListener("click", () => chrome.runtime.openOptionsPage());

init();
