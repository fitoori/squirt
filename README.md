# SQUIRT
*Spectra-Qualified Unified Inky Rendering Tools*

A collection of **four one-shot Python 3 scripts** for Raspberry Pi (or any Linux SBC)
that fetch or cycle images and show them on an **Inky e-paper display**  
(13.3″ Spectra-6 Impression, 7-colour Impression 7.3″, PHAT, WHAT, or any panel
detected by `inky.auto`).  
When no hardware is available, the scripts fall back to **headless mode** and
write a `*_preview.png` so you can still see the result.

static/
├── xkcd/          # random comics ( xkcd.py )
├── nasa/          # NASA imagery   ( nasa.py )
├── landscapes/    # art paintings  ( landscapes.py )
└── saved/         # your own pics  ( save.py )

---

## 1. Quick Start

```bash
# Clone / copy scripts onto your Pi
chmod +x xkcd.py nasa.py landscapes.py save.py

# Install Python deps once (system-wide or venv)
python3 -m pip install --user inky numpy requests beautifulsoup4 pillow

# Show a random XKCD:
./xkcd.py

# Show a random NASA picture of the day:
./nasa.py --apod        # default, flag optional

# Grab an unseen landscape (Met or AIC):
./landscapes.py --wide  # only landscape orientation

# Cycle through your own folder:
mkdir -p static/saved
cp *.jpg static/saved/
./save.py
```bash

Tip  If your Inky board has no EEPROM, set
INKY_TYPE=el133uf1 (13.3″ Spectra-6), phat, or what
near the top of each script.

⸻

2. Script Cheat-Sheet

Script	What it does	Common Flags
xkcd.py	Downloads a random XKCD comic	none
nasa.py	Shows a single NASA image	--apod (default) · --mars [ROVER] · --epic · --earth LAT LON [--dim] · --search "QUERY" · --key API_KEY
landscapes.py	Fetches an unseen landscape painting from The Met or AIC	--met / --aic · --wide / --tall · --reset
save.py	Cycles through static/saved/ or downloads a URL into that folder	[URL] · --folder DIR · --reset

All four scripts share:
	•	One-shot design – run, display once, exit cleanly (great for cron/systemd).
	•	Robust installs – auto-install inky/numpy silently if missing.
	•	Headless fallback – if no Inky detected, write a preview PNG instead.
	•	Consistent logging – prints saved path and diagnostics to stdout/stderr.

⸻

3. Hardware Notes
	•	Tested with Inky v2.1 (pip install inky>=2.1.0).
	•	Supports:
	•	InkyEL133UF1 – 13.3″ Spectra-6 Impression (1600 × 1200)
	•	InkyImpression73 – 7-colour 7.3″ panel
	•	InkyPHAT, InkyWHAT, and any board detected by inky.auto
	•	No EEPROM? Set INKY_TYPE constant near the top of each script.

GPIO/SPI must be enabled and user accessible (spidev, gpiod, etc.).

⸻

4. Folder Layout

Each script caches images in its own sub-folder, preventing clashes while
keeping everything under one static/ root for easy backup or gallery hosting.

static/
    xkcd/*.png
    nasa/*.jpg
    landscapes/*.jpg    + seen.json
    saved/*.jpg|.png    + last.txt

Delete any file and the script will simply re-download or skip it next time.

⸻

5. Scheduling Examples

systemd timer for a daily comic

```bash
# /etc/systemd/system/xkcd.service
[Service]
Type=oneshot
User=pi
WorkingDirectory=/home/pi/inky-scripts
ExecStart=/usr/bin/python3 /home/pi/inky-scripts/xkcd.py

# /etc/systemd/system/xkcd.timer
[Timer]
OnCalendar=*-*-* 08:00
Unit=xkcd.service

[Install]
WantedBy=timers.target

Enable with:

sudo systemctl daemon-reload
sudo systemctl enable --now xkcd.timer
```bash

⸻

6. Troubleshooting

Symptom	Resolution
Inky unavailable → headless mode: No module named 'inky…'	pip install --user inky numpy
No EEPROM detected! when using auto()	Define INKY_TYPE in the script (el133uf1, phat, what, …).
Preview PNG saves but display stays blank	Check SPI/I²C overlay, wiring, and run as a user in the gpio group.
NASA key quota exceeded	Get a free key at https://api.nasa.gov/ and call script with --key.


⸻

7. License

MIT © 2025 github.com/fitoori • Contributions welcome!

