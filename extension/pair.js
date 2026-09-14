/*
 * Video Grabber - automatic pairing.
 *
 * Runs on localhost pages and looks for the two meta tags the app emits. If
 * they are there, this is the downloader's own page, so take the server
 * address and token straight from it. That removes the copy-paste step from
 * the Options page, which was easy to get half-right: "Test connection" reads
 * the boxes rather than storage, so it could report success on a token that
 * was never saved.
 *
 * The match pattern cannot pin a port, so this does run on other localhost
 * apps. It reads nothing and stores nothing unless both tags are present.
 */

(function () {
  const tokenTag = document.querySelector('meta[name="video-grabber-token"]');
  const serverTag = document.querySelector('meta[name="video-grabber-server"]');
  if (!tokenTag || !serverTag) return;

  const token = (tokenTag.getAttribute("content") || "").trim();
  const server = (serverTag.getAttribute("content") || "").trim();
  if (!token || !server) return;

  chrome.storage.sync.get({ server: "", token: "" }, (existing) => {
    if (existing.server === server && existing.token === token) {
      announce("Extension already paired.");
      return;
    }
    chrome.storage.sync.set({ server, token }, () => {
      announce(
        chrome.runtime.lastError
          ? "Could not pair: " + chrome.runtime.lastError.message
          : "Extension paired automatically."
      );
    });
  });

  // The app leaves an empty slot for this; fall back to a floating note if the
  // page is an older build without one.
  function announce(text) {
    const slot = document.getElementById("pair-status");
    if (slot) {
      slot.textContent = text;
      return;
    }
    const note = document.createElement("div");
    note.textContent = text;
    note.style.cssText =
      "position:fixed;right:16px;bottom:16px;z-index:99999;padding:9px 14px;" +
      "border-radius:8px;background:#2f6feb;color:#fff;font:13px system-ui;" +
      "box-shadow:0 2px 10px rgba(0,0,0,.3)";
    document.body.appendChild(note);
    setTimeout(() => note.remove(), 6000);
  }
})();
