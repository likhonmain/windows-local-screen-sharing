# Local Screen Mirror

Mirror and control a Windows laptop display from Chrome on a OnePlus 15 or another Android phone. The laptop can be connected directly to the phone's Wi-Fi hotspot. No Android app, router, cloud account, screen recording, STUN server, or internet connection is used by the running mirror.

## Use

1. Enable the OnePlus Wi-Fi hotspot and connect the laptop to it. Use the hotspot's 5 GHz band if available and keep the devices nearby.
2. Double-click `Start-Mirror.cmd`. Keep its console window open. The laptop control panel opens at `http://127.0.0.1:8766`.
3. Scan the panel's QR code with your OnePlus camera, or use **Copy pairing link** and open that complete link in Chrome on the phone. The short address shown on the panel alone is not a pairing credential.
4. Double-click `Enable-Hotspot-Access.cmd` and accept the Windows administrator prompt. Run it again if upgrading from the image-only version: video needs UDP as well as the existing TCP page connection. The rules allow only the current laptop address and hotspot subnet, even on a Public network profile.
5. Rotate the phone and tap **Fullscreen**. Use Android's Back gesture/button or desktop Escape to leave fullscreen; there is no exit button over the picture. Start with **60 fps · 1080p smooth**, or select **120 fps · 720p speed** for the maximum target.

Install Python 3.10 or newer with Python added to PATH. The launcher creates a `.venv` and downloads the required libraries during first setup. Once installed, the mirror runs offline. You can keep mobile data off if your phone permits a hotspot without it; mirroring traffic itself does not use mobile data.

## Controls

- Laptop: choose the display, resolution and 30/60/90/120 FPS target; pause/resume sharing, enable/disable paired phone touch control, and copy the session pairing link.
- Phone: choose the same resolution and FPS targets, pause/resume its view, reconnect, and switch between fit/fill. Fill crops edges to fill the phone; fit preserves the whole screen. **Realtime video** is the default; **Image compatibility** remains available.
- Stop with **Stop server** in the laptop control panel, **Ctrl+C** in its console, or by closing that window. Pairing credentials become invalid when the process ends. A restart generates a new QR code.

### Touch gestures

Touch control is enabled for paired phones by default. The laptop's **Allow paired phone touch control** switch disables it for every viewer. The phone's **Touch on/off** button affects only that phone's gestures.

| Gesture | Windows action |
| --- | --- |
| Tap | Left click at that screen position |
| Double tap | Two clicks (Windows recognizes a double click) |
| Drag one finger | Hold the left button and drag |
| Hold still for about half a second | Right click |
| Move two fingers up/down | Scroll |

Touches map to the actual displayed picture in both fit and fill modes. Letterbox margins do not send clicks. Mouse buttons are released when sharing or touch is disabled, the selected display changes, a gesture is canceled, or a disconnected phone stops renewing a drag. Windows blocks simulated input into elevated administrator apps and secure prompts; run ordinary apps at their normal privilege level. Touch emulates mouse input, including scrolling; it is not native Windows multitouch and does not provide a phone keyboard.

### Frame rate and picture quality

The default transport is direct local WebRTC H.264 video over UDP, with Windows Graphics Capture and Intel Quick Sync hardware encoding when available. It avoids repeatedly compressing and sending full JPEG screenshots over TCP. Capture and transport take the newest frame instead of building a backlog. CPU H.264 and GDI capture are compatibility fallbacks. Touch uses the same local video connection, avoiding a separate HTTP round trip for every movement.

Choose a **30, 60, 90 or 120 FPS target** independently of resolution. The two speed presets set both resolution and FPS; other presets retain the current FPS selection. The phone shows actual decoded and displayed FPS, resolution and network round-trip time. The laptop shows capture and encoded FPS. These are measurements, not promises: display refresh rates and 5 GHz alone do not guarantee 120 FPS capture, encoding, decoding or browser presentation. For 120 FPS, start at 720p and compare the phone's counters; its browser may present at 60 Hz even when the panel supports 120 Hz.

A development benchmark on the tested Intel UHD laptop decoded about **54 FPS at 1080p** with a 60 FPS target and **91 FPS at 720p** with a 120 FPS target. Capture/encoding reached roughly 106 FPS in the latter test. This used a separate local software decoder on the same laptop, not the OnePlus; it does not measure Wi-Fi performance or phone presentation latency.

WebRTC uses adaptive H.264 compression. The image-quality percentages below apply to **Image compatibility**; realtime video uses the selected width and an adaptive video bitrate instead. Lossless always selects the PNG image transport at up to 8 FPS.

| Mode | Maximum width | Image compatibility quality | FPS selection |
| --- | ---: | --- | --- |
| 120 fps speed | 1280 px | JPEG 70% | Sets 120 |
| 60 fps smooth | 1920 px | JPEG 80% | Sets 60 |
| Data saver | 854 px | JPEG 45% | Retains selected FPS |
| Fast | 1024 px | JPEG 55% | Retains selected FPS |
| Balanced | 1600 px | JPEG 75% | Retains selected FPS |
| Smooth | 1920 px | JPEG 82% | Retains selected FPS |
| Sharp text | 2560 px | JPEG 92%, full chroma | Retains selected FPS |
| Ultra | 3840 px | JPEG 98%, full chroma | Retains selected FPS |
| Native | Original display size | JPEG 100%, full chroma | Retains selected FPS |
| Lossless | Original display size | PNG | Capped at 8 |

The mirror never upscales the source display. A 1080p laptop remains 1080p even in Ultra mode. Image compatibility is capped at 60 FPS and is usually slower than video. Native JPEG still uses compression; Lossless PNG preserves captured pixels and is best for reading text.

There is no audio or extended desktop. Protected video and Windows secure prompts may appear black. Keep the laptop awake and its lid open unless its power settings allow it to stay awake with the lid closed.

## Local connection and privacy

The phone listener binds only to the detected hotspot IPv4 address, not every adapter. It accepts only clients from that subnet and requires a random pairing credential for both the screen and touch input. Paired phones can control ordinary Windows apps while the laptop's touch switch is enabled, so share the pairing link only with trusted people. The control panel binds to loopback and is accessible only on the laptop. Screen capture runs only while a viewer is connected and sharing is enabled. Assets and QR generation are local; no external fonts, analytics, or other browser requests are included. Captured frames remain in memory and are not saved.

Pairing and page requests use HTTP on your password-protected hotspot; the application does not add HTTPS. WebRTC encrypts video with DTLS-SRTP and touch with a DTLS data channel. Both ICE endpoints use direct host candidates, with no STUN/TURN servers; the server restricts candidate addresses to its selected hotspot interface and the paired HTTP client's address. Keep the hotspot private and share its password and pairing link only with people you trust. No router port forwarding is needed. After changing hotspot addresses, restart the mirror and rerun the firewall helper.

To remove this app's firewall rule later, run in an administrator PowerShell:

```powershell
Remove-NetFirewallRule -Name LocalScreenMirror-Hotspot,LocalScreenMirror-Video
```

## Troubleshooting

- **Wrong adapter:** automatic detection prefers Wi-Fi with a gateway, ignoring VM adapters. To choose explicitly: `.venv\Scripts\python.exe server.py --bind YOUR_LAPTOP_IP --prefix 24`. Use the real subnet prefix from `Get-NetIPAddress` if different.
- **Phone cannot reach the laptop:** use the laptop's hotspot IP from the control panel, run the firewall helper, and temporarily disconnect a phone/laptop VPN if it blocks LAN traffic. Some phone hotspot implementations restrict access; actual OnePlus connectivity must be checked on the phone.
- **Page opens but video falls back to images:** rerun `Enable-Hotspot-Access.cmd` after starting the updated server, then tap **Reconnect**. The new video transport needs its scoped UDP firewall rule.
- **FPS below the target:** use realtime video, select the 720p speed preset, close extra viewing tabs and heavy laptop tasks, and check decoded/displayed FPS. If capture says GDI, the faster Windows capture backend could not initialize. If the phone decodes more frames than it displays, check its browser refresh-rate and battery-saving settings.
- **Page opens but says pairing required:** scan the current QR code or open the full copied link, including its fragment. A new server session needs a new pairing link.
- **Black picture or capture error:** unlock Windows and pick the correct display. Secure desktop prompts and protected media cannot be mirrored.
- **Disconnected after phone sleeps:** return to Chrome and tap Reconnect. Keep the phone screen on while viewing.
- **Port in use:** close the previous mirror or specify `--port 8875 --control-port 8876`, then rerun the firewall helper.

## Verify

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -v
node --test tests/touch.test.js
```

Python tests exercise pairing, local-only controls, origin/host restrictions, pause behavior, live JPEG and PNG captures, real H.264 WebRTC decoding, local-only ICE candidates, data-channel touch and connection cleanup. Input tests use a recording sender so tests do not click the real desktop. Node.js tests cover touch coordinates in fit/fill layouts and tap, drag, right-click, cancellation, and two-finger scrolling. Node.js is needed only for these frontend tests. Phone connectivity, presentation FPS and gestures still need a check from the actual phone.
