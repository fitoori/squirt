# SQUIRT  
_Spectra-Qualified Uncomplicated Inky Rendering Tools_
-
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

First install the inky libraries/modules: https://github.com/pimoroni/inky


0. Install apt dependencies
   ```bash
   sudo apt update && sudo apt install -y \
   git \
   python3-pip python3-setuptools python3-wheel python3-venv \
   python3-numpy python3-pil python3-spidev \
   python3-rpi.gpio python3-libgpiod \
   python3-smbus2 python3-lxml
2. Enable i2c and SPI
   ```bash
   sudo raspi-config nonint do_i2c 0 do_spi 0

4. Clone this repo, make scripts executable
   ```bash
   cd squirt && sudo chmod +x xkcd.py nasa.py landscapes.py save.py

6. Enable the venv that the inky script created
7. ```bash
   source ~/.virtualenvs/pimoroni/bin/activate
8. Install pip dependencies, if anything was missed. 
  ```bash
   pip3 install inky numpy requests beautifulsoup4 pillow

```


Usage
-
   ```bash
   python3 ./xkcd.py
```
Fetch and display a random XKCD comic.
   ```bash
   python3 ./nasa.py [--apod|--mars [ROVER]|--epic|--earth LAT LON [--dim]|--search "QUERY"] [--key API_KEY]
   ```
Display one NASA image.
   ```bash
   python3 /landscapes.py [--met|--aic] [--wide|--tall] [--reset]
   ```
Fetch an unseen landscape painting from The Met or AIC.
   ```bash	
   python3 ./save.py [URL] [--folder DIR] [--reset]
```
Download & show a image from URL, or cycle through static/saved/ if called without a flag.

Hardware Notes
-
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


