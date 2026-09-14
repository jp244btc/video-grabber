# Video Grabber

Download videos from the web with one click, using [yt-dlp](https://github.com/yt-dlp/yt-dlp)
under the hood. A small local app does the downloading; a browser extension
spots videos on the page and hands them over, the way Video DownloadHelper
works, except free, open, and with yt-dlp's site coverage.

Everything runs on your own PC. The only thing that leaves it is the request
that fetches the video.

## Install (Windows)

1. Download `VideoGrabber-Setup-<version>.exe` from the
   [latest release](https://github.com/jp244btc/video-grabber/releases/latest)
   and run it. No admin rights needed. Leave both boxes ticked: one starts the
   app hidden at sign-in, the other downloads ffmpeg (about 100 MB, needed for
   nearly every download).
2. Load the browser extension. In Edge or Chrome open `edge://extensions` or
   `chrome://extensions`, turn on **Developer mode**, click **Load unpacked**,
   and pick the `extension` folder. The Start Menu has a shortcut named
   **Video Grabber extension folder** that opens it for you.
3. Open the Video Grabber page once (Start Menu, or the popup's **Open full
   app** link). The extension pairs itself the moment that page loads.

That's it. From then on the app runs silently at sign-in, and the extension
icon shows a badge whenever the current tab is playing something.

Windows SmartScreen may warn about the installer because it is not signed with
a paid certificate. Click **More info**, then **Run anyway**.

## Using it

**From the extension.** Click the toolbar icon. Each video detected on the page
gets a card with a frame from the stream, its length, the resolutions on offer,
and a Download button that turns into a progress bar. Files are named
`<n> <page title>.mp4` in your Downloads folder. Start a video playing first;
nothing is listed until the page actually requests media. On a feed the list
keeps growing as you scroll.

Under the cards, **Grab page** hands the page's own URL to yt-dlp instead. On
a single-video page that is often the better route: yt-dlp knows the site,
picks the best rendition and names the file properly. On a feed it fails,
because there is no one video to pick.

**From the app page.** Paste any link, optionally look it up to choose a
quality, and download. Extras that the extension's cards do not expose:

- **Audio only**, as MP3, M4A, Opus or WAV.
- **Trim** a clip by start and end time. Cut points are re-encoded so the clip
  begins exactly where you asked, not at the nearest keyframe.
- **Exact format** from the full table yt-dlp reports.
- **Sign-in cookies**, for age-gated or members-only videos. Pick Firefox or
  point it at an exported `cookies.txt`. Chrome and Edge encrypt their cookie
  store in a way yt-dlp cannot read, so those two options will fail; that is
  a Chrome design decision, not a bug here.

## Keeping it working

Sites change their players constantly and a yt-dlp that is a few months old
starts failing on the big ones. The **Check for yt-dlp update** button at the
bottom of the app page fetches the newest release in place. Restart the app
afterwards. This is the first thing to try when a link that used to work stops.

If the extension says it cannot reach the downloader, the app is not running.
Start it from the Start Menu. If it keeps happening, look at
`%LOCALAPPDATA%\Video Grabber\video-grabber.log`.

## How the pieces fit

```
browser extension  ──HTTP, 127.0.0.1:5001──▶  local app (Flask)  ──▶  yt-dlp + ffmpeg  ──▶  Downloads
   sniffs media                                probes, downloads,
   shows cards                                 draws thumbnails
```

The app binds to `127.0.0.1` only, so nothing off your machine can reach it.
Because any web page you visit could still talk to localhost, every API call
must carry a token the app generated on first run. The extension picks that
token up automatically from the app page; a page from the open web cannot, so
it cannot make your PC download things behind your back.

## Running from source

```
git clone https://github.com/jp244btc/video-grabber
cd video-grabber
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe app.py
```

`run.bat` does the same with a first-run setup. `autostart-enable.bat` adds a
hidden-at-sign-in shortcut for the source checkout, mirroring what the
installer does. ffmpeg is found on `PATH`, in a winget install, or downloaded
from the button on the app page.

Flags: `--no-browser` (do not open a tab), `--port N` (if 5001 is taken by
something else), `--install-ffmpeg [zip]`, `--version`.

Running from source and from the installer at the same time is harmless: a
second launch notices the first one holding the port and exits.

## Building the installer

```
winget install JRSoftware.InnoSetup
powershell -ExecutionPolicy Bypass -File build\build.ps1
```

That produces `dist\VideoGrabber\` (portable) and
`dist\VideoGrabber-Setup-<version>.exe`. Pushing a `v*` tag runs the same
build on GitHub Actions and attaches the installer to a release.

The packaged build ships yt-dlp as a plain folder beside the exe rather than
frozen inside it, which is what lets the update button replace it without a
reinstall. ffmpeg is downloaded from the
[yt-dlp/FFmpeg-Builds](https://github.com/yt-dlp/FFmpeg-Builds) releases and
is not part of the installer.

## Layout

| Path | What it is |
| --- | --- |
| `app.py` | The local server: yt-dlp wiring, ffmpeg bootstrap, job tracking |
| `templates/index.html` | The app page, one file |
| `extension/` | The Chrome/Edge extension (Manifest V3) |
| `extension/background.js` | Media sniffing and the per-tab badge |
| `extension/popup.js` | The card popup |
| `extension/pair.js` | Reads the token off the app page so pairing is automatic |
| `extension/make_icons.py` | Generates the PNG and ICO icons from code |
| `installer/VideoGrabber.iss` | Inno Setup script |
| `build/build.ps1` | Freezes the app and builds the installer |
| `VideoGrabber.spec` | PyInstaller spec |

## Limits

- Windows only for the packaged build. The source runs anywhere Python and
  ffmpeg do, but the autostart scripts and installer are Windows-specific.
- Chrome and Edge only for the extension. Firefox declares background scripts
  differently under Manifest V3 and would need its own manifest.
- Live streams record until you cancel.
- Downloading is not the same as having the right to republish.

## License

MIT. ffmpeg builds fetched at install time are GPL and come from the
yt-dlp/FFmpeg-Builds project; yt-dlp is Unlicense.
