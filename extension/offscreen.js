/*
 * Video Grabber - tab recorder.
 *
 * Runs in an offscreen document, because a service worker has no
 * MediaRecorder and is stopped whenever it goes idle. The worker hands over a
 * tabCapture stream id; this turns it into a stream, plays its sound back so
 * the tab stays audible (capturing a tab silences it), and records it.
 * MediaRecorder hands over a slice every second, and each slice is posted to
 * the local app straight away and then let go, so an hour-long call costs the
 * browser a few seconds of buffer rather than an hour of video. The app
 * appends the slices and makes the MP4 when the recording stops.
 *
 * An offscreen document gets chrome.runtime and no other extension API, so
 * the worker passes the server address and token in with the stream id.
 */

const SLICE_MS = 1000;
const MAX_BACKLOG = 64 * 1024 * 1024;    // unsent bytes before the app counts as gone
const RETRY_FOR_MS = 60000;               // how long one slice keeps trying a silent app

// H.264 first: the app copies it into the MP4 as it is, which for an hour of
// video is the difference between seconds and many minutes. VP8 is the
// fallback every Chromium can record; the app re-encodes that.
const TYPES = ["video/webm;codecs=h264,opus", "video/webm;codecs=vp8,opus", "video/webm"];

let rec = null;

chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
  if (!msg || msg.target !== "offscreen") return false;
  const work = msg.type === "start" ? start(msg) : msg.type === "stop" ? stop() : null;
  if (!work) return false;
  work.then(reply, (e) => reply({ error: e.message }));
  return true;
});

async function call(cfg, path, init) {
  const res = await fetch(cfg.server.replace(/\/+$/, "") + path, {
    ...init,
    headers: { "X-VG-Token": cfg.token, ...(init.headers || {}) },
    // A wedged app must not hold the queue, and with it Stop, for ever.
    signal: AbortSignal.timeout(30000),
  });
  let data = {};
  try { data = await res.json(); } catch (e) { /* non-JSON error page */ }
  if (!res.ok) {
    const err = new Error(data.error || `Video Grabber said ${res.status}.`);
    err.status = res.status;
    throw err;
  }
  return data;
}

function post(cfg, path, body) {
  return call(cfg, path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

async function start({ streamId, server, token, title, url, width, height }) {
  if (rec) throw new Error("Already recording.");
  const cfg = { server, token };

  let media;
  try {
    media = await navigator.mediaDevices.getUserMedia({
      audio: { mandatory: { chromeMediaSource: "tab", chromeMediaSourceId: streamId } },
      video: {
        mandatory: {
          chromeMediaSource: "tab",
          chromeMediaSourceId: streamId,
          // One frame size for the whole file. Resizing the window letterboxes
          // the picture instead of changing the resolution halfway, which an
          // MP4 the app copies the video into could not follow.
          minWidth: width, maxWidth: width, minHeight: height, maxHeight: height,
          maxFrameRate: 30,
        },
      },
    });
  } catch (e) {
    throw new Error(`Could not capture the tab: ${e.message}`);
  }

  let id;
  try {
    ({ id } = await post(cfg, "/api/record/start", { title, url }));
  } catch (e) {
    media.getTracks().forEach((t) => t.stop());
    throw e;
  }

  try {
    // Capturing a tab silences it. Playing the captured sound back out is
    // what keeps a call audible while it records.
    const audio = new AudioContext();
    if (media.getAudioTracks().length) audio.createMediaStreamSource(media).connect(audio.destination);
    // Never awaited outright: a context the autoplay rules hold back
    // resumes on no schedule at all, and the recording must not wait for it.
    await Promise.race([audio.resume(), new Promise((r) => setTimeout(r, 500))]).catch(() => {});

    const recorder = new MediaRecorder(media, {
      mimeType: TYPES.find((t) => MediaRecorder.isTypeSupported(t)) || "",
      videoBitsPerSecond: 2500000,
      audioBitsPerSecond: 128000,
      // A keyframe every two seconds keeps an hour-long MP4 seekable even
      // though its video is copied, not re-encoded. Ignored where unknown.
      videoKeyFrameIntervalDuration: 2000,
    });

    const r = { cfg, id, media, audio, recorder, seq: 0, backlog: 0, sending: Promise.resolve(), failed: "", asked: false };
    r.done = new Promise((resolve) => { r.finished = resolve; });
    recorder.ondataavailable = (e) => { if (e.data && e.data.size) send(r, e.data); };
    recorder.onerror = (e) => fail(r, `The recorder failed: ${(e.error && e.error.message) || "unknown error"}.`);
    recorder.onstop = () => finish(r);
    // The tab closed, or the browser took the capture back: the recording
    // ends there and is saved like any other.
    for (const track of media.getTracks()) track.addEventListener("ended", () => halt(r));
    recorder.start(SLICE_MS);
    rec = r;
    return { id, mimeType: recorder.mimeType, audio: audio.state };
  } catch (e) {
    media.getTracks().forEach((t) => t.stop());
    post(cfg, "/api/record/stop", { id, discard: true }).catch(() => {});
    throw new Error(`Could not start recording: ${e.message}`);
  }
}

function stop() {
  const r = rec;
  if (!r) return Promise.resolve({ error: "Nothing is recording." });
  r.asked = true;
  halt(r);
  return r.done;
}

function halt(r) {
  if (r.recorder.state !== "inactive") r.recorder.stop();
}

function fail(r, message) {
  if (r.failed) return;
  r.failed = message;
  halt(r);
}

// Slices go up one at a time and in order; each is dropped once the app has it.
function send(r, blob) {
  if (r.failed) return;
  const seq = r.seq++;
  r.backlog += blob.size;
  if (r.backlog > MAX_BACKLOG) {
    fail(r, "Video Grabber stopped taking the recording.");
    return;
  }
  r.sending = r.sending
    .then(() => (r.failed ? null : upload(r, seq, blob)))
    .then(
      () => { r.backlog -= blob.size; },
      (e) => fail(r, `Video Grabber stopped taking the recording (${e.message})`),
    );
}

async function upload(r, seq, blob) {
  const deadline = Date.now() + RETRY_FOR_MS;
  for (let wait = 500; ; wait = Math.min(wait * 2, 8000)) {
    try {
      return await call(r.cfg, `/api/record/chunk?id=${encodeURIComponent(r.id)}&seq=${seq}`, {
        method: "POST",
        headers: { "Content-Type": "application/octet-stream" },
        body: blob,
      });
    } catch (e) {
      // An answer means the app is up and said no; only silence is worth
      // waiting out, and not for ever.
      if ((e.status && e.status < 500) || Date.now() + wait > deadline) throw e;
    }
    await new Promise((resolve) => setTimeout(resolve, wait));
  }
}

// MediaRecorder's stop event comes after its last slice, so by the time the
// queue drains the app has everything.
async function finish(r) {
  r.media.getTracks().forEach((t) => t.stop());
  r.audio.close().catch(() => {});
  await r.sending;
  const result = { id: r.id };
  try {
    result.jobId = (await post(r.cfg, "/api/record/stop", { id: r.id })).job_id;
  } catch (e) {
    if (!r.failed) r.failed = `Could not finish the recording: ${e.message}`;
  }
  if (r.failed) result.error = r.failed;
  if (rec === r) rec = null;
  r.finished(result);
  // It ended on its own (the tab closed, the app went away), so nobody is
  // waiting for this answer: tell the worker.
  if (!r.asked) chrome.runtime.sendMessage({ type: "recEnded", ...result }).catch(() => {});
}
