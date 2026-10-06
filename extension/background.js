/*
 * Video Grabber - background service worker.
 *
 * Watches network traffic for playable media and keeps a per-tab list, so the
 * popup can offer whatever this page is actually streaming. The list lives in
 * chrome.storage.session because an MV3 service worker is killed once it goes
 * idle, which would otherwise wipe an in-memory map.
 *
 * Two things this has to get right on a site like X, or it silently misses
 * everything after the first screenful:
 *
 *  1. Once the site's own service worker is warm, the player's requests come
 *     from that worker and Chrome reports them with tabId -1. They still carry
 *     an initiator (the site's origin), so they are attributed to the active
 *     tab on that origin instead of being dropped.
 *
 *  2. A single-page app changes the URL constantly without loading a new
 *     page. The list is only reset on a real top-level navigation
 *     (webNavigation.onCommitted), never on a history push.
 *
 * And one thing it has to get right on TikTok and YouTube, or every card is
 * a three-second silent fragment: those players stream through Media Source
 * Extensions, so the card has to be the page, not anything the browser
 * fetched. See PAGE_SITES.
 */

// Whole files and stream manifests worth offering.
const MEDIA_URL_RE = /\.(m3u8|mpd|mp4|webm|m4v|mov|avi|flv|mkv|mp3|m4a|aac|flac|ogg|opus|wav)(\?|#|$)/i;
const MANIFEST_RE = /\.(m3u8|mpd)(\?|#|$)/i;

// HLS/DASH segments. A page streams thousands of these; the manifest above is
// the thing actually worth downloading, so they are noise.
const SEGMENT_RE = /\.(ts|m4s)(\?|#|$)|\/seg(ment)?[-_]?\d+|init\.mp4$/i;

const MEDIA_TYPE_RE = /^(video|audio)\/|mpegurl|dash\+xml|f4m|smil/i;

const REQUEST_TYPES = ["media", "xmlhttprequest", "object", "other"];
const MAX_PER_TAB = 40;
const KEY = (tabId) => `tab_${tabId}`;

// ---------------------------------------------------------------------------
// Classification helpers
// ---------------------------------------------------------------------------

function kindOf(url, contentType) {
  if (/\.m3u8(\?|#|$)/i.test(url) || /mpegurl/i.test(contentType || "")) return "HLS stream";
  if (/\.mpd(\?|#|$)/i.test(url) || /dash/i.test(contentType || "")) return "DASH stream";
  if (/^audio\//i.test(contentType || "")) return "Audio";
  const ext = (url.split("?")[0].split("#")[0].match(/\.([a-z0-9]{2,5})$/i) || [])[1];
  return ext ? ext.toUpperCase() : "Media";
}

/*
 * A stable id for "the same video", so quality variants of one clip collapse
 * to a single row. A busy timeline otherwise lists near-identical entries that
 * are impossible to tell apart.
 */
/*
 * Facebook's CDN chunks all share one directory, so the directory rule below
 * would fold every video on a page into a single card. The efg parameter is
 * base64 JSON naming the video; that id is the right grouping key, and the
 * app uses the same id to fetch the whole video from the watch page.
 */
function fbVideoId(u) {
  if (!/(^|\.)fbcdn\.net$/i.test(u.hostname)) return "";
  const efg = u.searchParams.get("efg");
  if (!efg) return "";
  try {
    const meta = JSON.parse(atob(efg.replace(/-/g, "+").replace(/_/g, "/")));
    return String(meta.video_id || meta.xpv_asset_id || "");
  } catch (e) {
    return "";
  }
}

function mediaKey(url) {
  try {
    const u = new URL(url);
    const fb = fbVideoId(u);
    if (fb) return "facebook:" + fb;
    const m = u.pathname.match(/\/(?:amplify_video|ext_tw_video|tweet_video)\/(\d+)/);
    if (m) return u.host + ":" + m[1];
    // Otherwise the containing directory, which groups renditions of one asset.
    return u.host + u.pathname.replace(/\/[^/]*$/, "");
  } catch (e) {
    return url;
  }
}

// Something short and human to tell rows apart in the popup.
function hintFor(url) {
  try {
    const u = new URL(url);
    const fb = fbVideoId(u);
    if (fb) return "facebook #" + fb.slice(-6);
    const m = u.pathname.match(/\/(?:amplify_video|ext_tw_video|tweet_video)\/(\d+)/);
    if (m) return u.host.replace(/^www\./, "") + " #" + m[1].slice(-6);
    const last = u.pathname.split("/").filter(Boolean).pop() || "";
    return u.host.replace(/^www\./, "") + (last ? " / " + last.slice(0, 28) : "");
  } catch (e) {
    return "";
  }
}

/*
 * Sites whose player streams through Media Source Extensions. The browser
 * never asks for a whole file: TikTok's worker pulls byte ranges of separate
 * video and audio MP4 tracks, YouTube's pulls UMP chunks from googlevideo. A
 * sniffed URL is therefore a silent, header-less slice, and nothing in it
 * names the video (Facebook's chunks at least carry the id in efg; these
 * carry only signatures). The document playing it does name the video, when
 * it is a video page, and yt-dlp knows both sites, so the card becomes the
 * page - the same end result as a Facebook chunk mapping back to its watch
 * page. On a feed or a profile the document URL names nothing, and no card
 * is better than a card that downloads a fragment.
 *
 *   media  the request is one of this site's media slices
 *   page   matches the document URL and captures the video id
 *   link   the clean URL to hand to the app
 */
const PAGE_SITES = [
  {
    name: "tiktok",
    media: (u) => /(^|\.)tiktok(cdn(-us|-eu)?)?\.com$/i.test(u.hostname) && /\/video\/tos\//i.test(u.pathname),
    page: /^https?:\/\/(?:www\.)?tiktok\.com\/(?:(?<user>@[^/?#]+)\/video|embed(?:\/v2)?|player\/v1)\/(?<id>\d+)/i,
    // yt-dlp takes the video page or embed/<id>, not the embed/v2 and
    // player/v1 shapes an embedded player's frame actually has.
    link: (m) => (m.groups.user ? m[0] : "https://www.tiktok.com/embed/" + m.groups.id),
  },
  {
    name: "youtube",
    media: (u) => /(^|\.)googlevideo\.com$/i.test(u.hostname) && /^\/videoplayback/i.test(u.pathname),
    page: /^https?:\/\/(?:(?:www\.|m\.|music\.)?youtube(?:-nocookie)?\.com\/(?:watch\?(?:[^#]*&)?v=|shorts\/|live\/|embed\/|v\/)|youtu\.be\/)(?<id>[\w-]{11})/i,
    link: (m) => "https://www.youtube.com/watch?v=" + m.groups.id,
  },
];

function pageSiteFor(url) {
  try {
    const u = new URL(url);
    return PAGE_SITES.find((s) => s.media(u)) || null;
  } catch (e) {
    return null;
  }
}

// The URL of the document a request was made for. Inside an iframe (an
// embedded player on someone else's page) that is the frame's own URL; the
// tab's URL would name the wrong thing.
async function documentUrlFor(details, tabId) {
  if (details.tabId >= 0 && details.frameId > 0) {
    try {
      const frame = await chrome.webNavigation.getFrame({ tabId, frameId: details.frameId });
      if (frame && frame.url) return frame.url;
    } catch (e) { /* frame already gone */ }
  }
  try { return (await chrome.tabs.get(tabId)).url || ""; } catch (e) { return ""; }
}

async function pageItem(site, details, tabId) {
  const doc = await documentUrlFor(details, tabId);
  const m = doc.match(site.page);
  if (!m) return null;
  const id = m.groups.id;
  return {
    url: site.link(m),
    key: site.name + ":" + id,
    kind: "MP4",
    hint: site.name + " #" + id.slice(-6),
    size: "",
    at: Date.now(),
  };
}

function prettySize(bytes) {
  const n = Number(bytes);
  if (!n || Number.isNaN(n)) return "";
  const units = ["B", "KB", "MB", "GB"];
  let v = n;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  return (i === 0 ? v.toFixed(0) : v.toFixed(1)) + " " + units[i];
}

// ---------------------------------------------------------------------------
// Storage. Writes are serialised: a player asks for its audio and video
// playlists in the same instant, and two concurrent read-modify-write cycles
// on the same list would lose one of them.
// ---------------------------------------------------------------------------

let queue = Promise.resolve();
function serialised(fn) {
  queue = queue.then(fn, fn);
  return queue;
}

async function record(tabId, item) {
  const key = KEY(tabId);
  const store = await chrome.storage.session.get(key);
  const entry = store[key] || { items: [] };

  // One row per video, not per rendition or per repeated request.
  if (entry.items.some((i) => i.key === item.key)) return;

  entry.items.unshift(item);
  if (entry.items.length > MAX_PER_TAB) entry.items.length = MAX_PER_TAB;
  await chrome.storage.session.set({ [key]: entry });

  chrome.action.setBadgeBackgroundColor({ color: "#2f6feb" });
  chrome.action.setBadgeText({ tabId, text: String(entry.items.length) });
}

async function clearTab(tabId) {
  await chrome.storage.session.remove(KEY(tabId));
  try { await chrome.action.setBadgeText({ tabId, text: "" }); } catch (e) { /* tab gone */ }
}

// ---------------------------------------------------------------------------
// Which tab does a request belong to?
// ---------------------------------------------------------------------------

async function tabsForRequest(details) {
  if (details.tabId >= 0) return [details.tabId];

  // No tab id: the request came from the site's service worker. Its origin
  // is still known, so hand it to the tab(s) showing that site - the active
  // one in each window if there is one, otherwise all of them.
  let origin = details.initiator || "";
  if (!origin && details.documentUrl) {
    try { origin = new URL(details.documentUrl).origin; } catch (e) { /* ignore */ }
  }
  if (!origin) return [];

  const tabs = await chrome.tabs.query({});
  const onSite = tabs.filter((t) => {
    try { return new URL(t.url || "").origin === origin; } catch (e) { return false; }
  });
  if (!onSite.length) return [];
  const active = onSite.filter((t) => t.active);
  return (active.length ? active : onSite).map((t) => t.id);
}

// ---------------------------------------------------------------------------
// Detection
// ---------------------------------------------------------------------------

function consider(details, contentType, length) {
  const url = details.url;
  if (!/^https?:/i.test(url)) return;
  if (SEGMENT_RE.test(url)) return;

  // A slice from a Media Source player: the card is the page it plays on.
  // Decided before the media checks because YouTube's slices do not even
  // look like media (application/vnd.yt-ump, no extension).
  const site = pageSiteFor(url);
  if (site) {
    serialised(async () => {
      for (const tabId of await tabsForRequest(details)) {
        const item = await pageItem(site, details, tabId);
        if (item) await record(tabId, item);
      }
    });
    return;
  }

  const isManifest = MANIFEST_RE.test(url) || /mpegurl|dash/i.test(contentType || "");
  const looksMedia = isManifest || MEDIA_TYPE_RE.test(contentType || "") || MEDIA_URL_RE.test(url);
  if (!looksMedia) return;

  // Skip trivially small files - usually a poster or a tracking pixel
  // mislabelled, not something worth downloading. Manifests are tiny by
  // nature, so they are exempt.
  if (!isManifest && length && Number(length) < 40000) return;

  const item = {
    url,
    key: mediaKey(url),
    kind: kindOf(url, contentType),
    hint: hintFor(url),
    size: prettySize(length),
    at: Date.now(),
  };

  serialised(async () => {
    for (const tabId of await tabsForRequest(details)) {
      await record(tabId, item);
    }
  });
}

// Fires for every request, cached or not, but knows only the URL. Enough to
// catch manifests, which are what a streaming player asks for.
chrome.webRequest.onBeforeRequest.addListener(
  (details) => {
    if (MANIFEST_RE.test(details.url)) consider(details, "", "");
  },
  { urls: ["<all_urls>"], types: REQUEST_TYPES }
);

// Fires once headers are in, including for responses served from cache, so
// files can be judged by content type and size.
chrome.webRequest.onResponseStarted.addListener(
  (details) => {
    let contentType = "";
    let length = "";
    for (const h of details.responseHeaders || []) {
      const name = h.name.toLowerCase();
      if (name === "content-type") contentType = h.value || "";
      else if (name === "content-length") length = h.value || "";
    }
    consider(details, contentType, length);
  },
  { urls: ["<all_urls>"], types: REQUEST_TYPES },
  ["responseHeaders"]
);

// ---------------------------------------------------------------------------
// Lifecycle. Only a real page load resets the list; a single-page app pushing
// a new URL does not fire onCommitted, so scrolling a feed keeps accumulating.
// ---------------------------------------------------------------------------

chrome.webNavigation.onCommitted.addListener((details) => {
  if (details.frameId !== 0) return;
  serialised(() => clearTab(details.tabId));
});

chrome.tabs.onRemoved.addListener((tabId) => {
  serialised(() => chrome.storage.session.remove(KEY(tabId)));
});

// The popup asks for the current tab's finds rather than reading storage
// itself, so the shape stays in one place.
chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
  if (msg && msg.type === "getFinds") {
    chrome.storage.session.get(KEY(msg.tabId)).then((store) => {
      reply((store[KEY(msg.tabId)] || { items: [] }).items);
    });
    return true;          // keep the channel open for the async reply
  }
  if (msg && msg.type === "clearFinds") {
    serialised(() => clearTab(msg.tabId)).then(() => reply(true));
    return true;
  }
  return false;
});
