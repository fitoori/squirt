# SQUIRT  
Spectra-Qualified Unified Inky Rendering Tools

A set of four one-shot Python 3 scripts for Raspberry Pi (or any Linux SBC)  
that fetch or cycle images and display them on an Inky e-paper panel  
(13.3″ Spectra-6 Impression, 7-colour Impression 7.3″, PHAT, WHAT, or any board detected by `inky.auto`).  
When no hardware is present, each script falls back to headless mode and writes a `*_preview.png`.

## Features

- **One-shot design**  
  Run once, display one image, exit cleanly (ideal for cron or systemd).
- **Auto-install**  
  Installs `inky` & `numpy` under your Python interpreter if missing.
- **Headless fallback**  
  Generates a `*_preview.png` when no Inky hardware is found.
- **Consistent helpers**  
  Shared functions for HTTP, image-fitting, Inky detection, and CLI parsing.

---

## Installation

1. Copy or clone the scripts to your Pi:
   ```bash
   chmod +x xkcd.py nasa.py landscapes.py save.py

2.	Install dependencies (system-wide or in a venv):

python3 -m pip install --user inky numpy requests beautifulsoup4 pillow


3.	Ensure SPI/I²C are enabled and you have GPIO permissions (spidev, gpiod, etc.).

⸻

Usage

xkcd.py

Fetch and display a random XKCD comic.

./xkcd.py

nasa.py

Display one NASA image:

./nasa.py [--apod|--mars [ROVER]|--epic|--earth LAT LON [--dim]|--search "QUERY"] [--key API_KEY]

landscapes.py

Fetch an unseen landscape painting from The Met or AIC:

./landscapes.py [--met|--aic] [--wide|--tall] [--reset]

save.py

Cycle through static/saved/, or download & show a URL:

./save.py [URL] [--folder DIR] [--reset]


⸻

Script Cheat-Sheet

Script	Description	Flags / Args
xkcd.py	Random XKCD comic	none
nasa.py	Single NASA image	--apod, --mars [ROVER], --epic, --earth LAT LON [--dim],--search "QUERY", --key API_KEY
landscapes.py	Unseen landscape painting (Met / AIC)	--met, --aic, --wide, --tall, --reset
save.py	Next image in folder or download a URL into saved	[URL], --folder DIR, --reset


⸻

Hardware Notes
	•	Tested with inky v2.1 (pip install inky>=2.1.0).
	•	Supports:
	•	InkyEL133UF1 (13.3″ Spectra-6 Impression)
	•	InkyImpression73 (7-colour 7.3″ Impression)
	•	InkyPHAT, InkyWHAT
	•	Any board auto-detected by inky.auto()
	•	If your board lacks EEPROM, set INKY_TYPE (e.g. el133uf1, phat, what) near the top of each script.

⸻
License

MIT © 2025 YorozuyaTech
Contributions welcome!


