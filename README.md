# Local Screen Mirror

Mirror and control a Windows laptop display from Chrome on a OnePlus 15 or another Android phone. The laptop can be connected directly to the phone's Wi-Fi hotspot. No Android app, router, cloud account, screen recording, STUN server, or internet connection is used by the running mirror.

## Use

1. Enable the OnePlus Wi-Fi hotspot and connect the laptop to it. Use the hotspot's 5 GHz band if available and keep the devices nearby.
2. Double-click `Start-Mirror.cmd`. Keep its console window open. The laptop control panel opens at `http://127.0.0.1:8766`.
3. Scan the panel's QR code with your OnePlus camera, or use **Copy pairing link** and open that complete link in Chrome on the phone. The short address shown on the panel alone is not a pairing credential.
4. If Chrome cannot connect, double-click `Enable-Hotspot-Access.cmd`, accept the Windows administrator prompt, and reopen the link. This adds an inbound firewall rule for the current laptop address and hotspot subnet only. It works even if Windows labels the hotspot Public; there is no need to change the network profile.
5. Rotate the phone and tap **Fullscreen**. Use Android's Back gesture/button or desktop Escape to leave fullscreen; there is no exit button over the picture. Try **Smooth** for motion, **Sharp text** for reading, or **Native** / **Lossless** for the full display resolution.

Install Python 3.10 or newer with Python added to PATH. The launcher creates a `.venv` and downloads the required libraries during first setup. Once installed, the mirror runs offline. You can keep mobile data off if your phone permits a hotspot without it; mirroring traffic itself does not use mobile data.

## Controls

- Laptop: choose the display and quality, pause/resume sharing, enable/disable paired phone touch control, and copy the session pairing link.
- Phone: pause/resume its view, change quality, reconnect, and switch between fit/fill. Fill crops edges to fill the phone; fit preserves the whole screen.
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

### Picture quality

| Mode | Maximum width | Encoding | Target fps |
| --- | ---: | --- | ---: |
| Data saver | 854 px | JPEG 45% | 12 |
| Fast | 1024 px | JPEG 55% | 20 |
| Balanced | 1600 px | JPEG 75% | 20 |
| Smooth | 1920 px | JPEG 82% | 30 |
| Sharp text | 2560 px | JPEG 92%, full chroma | 15 |
| Ultra | 3840 px | JPEG 98%, full chroma | 15 |
| Native | Original display size | JPEG 100%, full chroma | 15 |
| Lossless | Original display size | PNG | 8 |

The mirror never upscales the source display. A 1080p laptop remains 1080p even in Ultra mode. Native JPEG still uses JPEG compression; Lossless PNG preserves the captured pixels. Higher quality uses more local Wi-Fi bandwidth and CPU, and actual fps depends on the display resolution, laptop, hotspot, and phone. Lossless is best for reading text and can feel slower.

This is a local image stream for desktop work and reading. It does not carry audio, create an extended desktop, or target gaming latency. Protected video and Windows secure prompts may appear black. Keep the laptop awake and its lid open unless its power settings allow it to stay awake with the lid closed.

## Local connection and privacy

The phone listener binds only to the detected hotspot IPv4 address, not every adapter. It accepts only clients from that subnet and requires a random pairing credential for both the screen and touch input. Paired phones can control ordinary Windows apps while the laptop's touch switch is enabled, so share the pairing link only with trusted people. The control panel binds to loopback and is accessible only on the laptop. Screen capture runs only while a viewer is connected and sharing is enabled. Assets and QR generation are local; no external fonts, analytics, or other browser requests are included. Captured frames remain in memory and are not saved.

The stream uses HTTP on your password-protected hotspot. The application does not add TLS encryption. Keep the hotspot private and share its password and pairing link only with people you trust. No router port forwarding is needed. After changing hotspot addresses, restart the mirror and rerun the firewall helper.

To remove this app's firewall rule later, run in an administrator PowerShell:

```powershell
Remove-NetFirewallRule -Name LocalScreenMirror-Hotspot
```

## Troubleshooting

- **Wrong adapter:** automatic detection prefers Wi-Fi with a gateway, ignoring VM adapters. To choose explicitly: `.venv\Scripts\python.exe server.py --bind YOUR_LAPTOP_IP --prefix 24`. Use the real subnet prefix from `Get-NetIPAddress` if different.
- **Phone cannot reach the laptop:** use the laptop's hotspot IP from the control panel, run the firewall helper, and temporarily disconnect a phone/laptop VPN if it blocks LAN traffic. Some phone hotspot implementations restrict access; actual OnePlus connectivity must be checked on the phone.
- **Page opens but says pairing required:** scan the current QR code or open the full copied link, including its fragment. A new server session needs a new pairing link.
- **Black picture or capture error:** unlock Windows and pick the correct display. Secure desktop prompts and protected media cannot be mirrored.
- **Disconnected after phone sleeps:** return to Chrome and tap Reconnect. Keep the phone screen on while viewing.
- **Port in use:** close the previous mirror or specify `--port 8875 --control-port 8876`, then rerun the firewall helper.

## Verify

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -v
node --test tests/touch.test.js
```

Python tests exercise pairing, local-only controls, origin/host restrictions, pause behavior, live JPEG and PNG captures, touch validation and permissions, coordinate mapping across multiple monitors, automatic mouse-button release, and QR generation. Input tests use a recording sender so tests do not click the real desktop. Node.js tests cover touch coordinates in fit/fill layouts and tap, drag, right-click, cancellation, and two-finger scrolling. Node.js is needed only for these frontend tests. Phone connectivity and gestures still need a check from the actual phone.
