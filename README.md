# SQUIRT
_Spectra-Qualified Uncomplicated Inky Rendering Tools_

A suite of one-shot Python 3 and shell utilities for Raspberry Pi (or any Linux SBC)
that render curated content on Pimoroni Inky e-paper panels. When no Inky hardware
is detected, the scripts fall back to saving PNG previews.

---

## Features

- **One-shot design** – run once, display one image, exit cleanly (ideal for cron or systemd).
- **Auto-install** – installs `inky` & `numpy` under your Python interpreter if missing.
- **Headless fallback** – generates a `*_preview.png` when no Inky hardware is found.
- **Shared helpers** – consistent HTTP, image-fitting, Inky detection, and CLI parsing.
- **Offline cycling** – fetched images are archived so content keeps rotating without internet.

---

## Scripts at a glance

| Script | Purpose | Quick start |
| --- | --- | --- |
| `xkcd.py` | Display a random XKCD comic with orientation-aware caching. | `python3 xkcd.py [--landscape|--portrait]` |
| `nasa.py` | Fetch NASA imagery (APOD, Mars, EPIC, Earth, or search) and display it. | `python3 nasa.py [--apod|--mars|--epic|--earth LAT LON|--search "QUERY"]` |
| `landscapes.py` | Rotate through landscape paintings from The Met, AIC, or CMA. | `python3 landscapes.py [--wide|--tall] [--met|--aic|--cma]` |
| `save.py` | Cycle local images or fetch one URL, with optional grayscale and fit modes. | `python3 save.py [URL] [--folder DIR] [--reset]` |
| `status.py` | Show system health, connectivity, and PiSugar/RTC status. | `python3 status.py [--force-triangle] [--no-pisugar] [--delay SEC]` |
| `webui.py` | Flask/Quart-style dashboard for driving the other scripts. | `python3 webui.py` (or use `./run-web.sh`) |
| `greeting.sh` | TTY-safe login banner summarising uptime, disk, Wi-Fi, and Unison status. | `./greeting.sh` (auto-exits when not in a TTY) |
| `sync.sh` | Wi‑Fi–gated Unison backup with clear Result lines and AppleDouble cleanup. | `./sync.sh` (configure variables inside first) |
| `run-web.sh` | Helper wrapper to start the web UI from cron/systemd. | `./run-web.sh` |

---

## Installation

1. **Install apt dependencies**
    ```bash
    sudo apt update && sudo apt install -y \
      git \
      python3-pip python3-setuptools python3-wheel python3-venv \
      python3-numpy python3-pil python3-spidev \
      python3-rpi.gpio python3-libgpiod \
      python3-smbus2 python3-lxml
    ```

2. **Install Inky libraries**  
   Follow the [official Pimoroni Inky instructions](https://github.com/pimoroni/inky).

3. **Enable I2C and SPI**
    ```bash
    sudo raspi-config nonint do_i2c 0 do_spi 0
    ```

4. **Clone this repo and make scripts executable**
    ```bash
    git clone https://github.com/fitoori/squirt.git
    cd squirt
    chmod +x *.py *.sh
    ```

5. **(Optional) Activate the Pimoroni virtualenv**
    ```bash
    source ~/.virtualenvs/pimoroni/bin/activate
    ```

6. **Install pip dependencies (if anything was missed)**
    ```bash
    pip3 install inky numpy requests beautifulsoup4 pillow
    ```

---

## Script details & usage

### `xkcd.py`
- **Purpose:** Fetch a random XKCD comic, cache it, and respect panel orientation.
- **Usage:**
  ```bash
  python3 ./xkcd.py [--landscape|--portrait]
  ```
- **Notes:** Maintains `static/xkcd/` with cached comics and `seen.json`. Headless runs save `*_preview.png`.

### `nasa.py`
- **Purpose:** Display a single NASA image from multiple sources.
- **Usage:**
  ```bash
  python3 ./nasa.py [--apod|--mars [ROVER]|--epic|--earth LAT LON [--dim]|--search "QUERY"] \
    [--key API_KEY] [--batch N] [--portrait|--landscape]
  ```
- **Notes:** Archives images in `static/nasa/` (auto-sorted into ratio folders). Uses `NASA_API_KEY` env var or DEMO_KEY.

### `landscapes.py`
- **Purpose:** Show a new landscape painting from The Met, AIC, or CMA, with orientation filters.
- **Usage:**
  ```bash
  python3 ./landscapes.py [--wide|--tall] [--met|--aic|--cma] [--mode fill|fit] [--reset]
  ```
- **Notes:** Caches artwork in `static/landscapes/`, remembers seen IDs, and falls back to local cache when offline.

### `save.py`
- **Purpose:** Cycle through local images or fetch a single URL into the cache.
- **Usage:**
  ```bash
  python3 ./save.py [URL] [--folder DIR] [--reset] [--delete NAME|INDEX] [--list] [--info NAME]
  ```
- **Notes:** Defaults to `static/saved/`, supports optional grayscale and fit modes, and writes previews when headless.

### `status.py`
- **Purpose:** Render a status splash showing uptime, disk, CPU, Wi‑Fi strength, and PiSugar/RTC health.
- **Usage:**
  ```bash
  python3 ./status.py [--force-triangle] [--no-triangle] [--no-pisugar] [--delay SEC]
  ```
- **Notes:** Stores logs and previews in `static/status/`. Use `STATUS_DELAY` env var to control the boot delay.

### `webui.py` and `run-web.sh`
- **Purpose:** Provide a small web dashboard for triggering the image scripts.
- **Usage:**
  ```bash
  python3 ./webui.py
  # or
  ./run-web.sh
  ```
- **Notes:** `run-web.sh` activates the Pimoroni virtualenv if present and then launches `webui.py` from the repo root.

### `greeting.sh`
- **Purpose:** Friendly, TTY-aware login banner summarising system health and Unison backup status.
- **Usage:**
  ```bash
  ./greeting.sh
  ```
- **Notes:** Exits silently when not attached to an interactive terminal. Reads Unison logs from `$HOME/unison_backup.log`.

### `sync.sh`
- **Purpose:** Wi‑Fi–aware Unison backup with bounded timeouts and clear `Result:` lines for logging.
- **Usage:**
  ```bash
  ./sync.sh
  ```
- **Notes:** Configure the variables at the top (Wi‑Fi interface, Unison profile, NFS mount points, thresholds) before running.
  Emits status, reason, and basic telemetry to `$HOME/unison_backup.log`.

---

## Scheduling examples

Use cron to refresh content automatically:
```bash
crontab -e
# Refresh XKCD hourly and show status + NASA on boot
0 * * * * python3 /home/$USER/squirt/xkcd.py
@reboot python3 /home/$USER/squirt/status.py --delay 10 && sleep 30 && python3 /home/$USER/squirt/nasa.py --apod &
```

---

## Run the web UI at boot

1) **systemd service (recommended)**
```bash
# /home/<your-username>/squirt/run-web.sh
#!/bin/bash
set -e
source /home/<your-username>/.virtualenvs/pimoroni/bin/activate || true
cd /home/<your-username>/squirt
exec python3 webui.py
```
Make it executable:
```bash
chmod +x /home/<your-username>/squirt/run-web.sh
```
Create `/etc/systemd/system/squirt-web.service` (adjust User=):
```ini
[Unit]
Description=SQUIRT web UI
After=network.target

[Service]
Type=simple
User=<your-username>
WorkingDirectory=/home/<your-username>/squirt
ExecStart=/home/<your-username>/squirt/run-web.sh
Restart=on-failure
RestartSec=5
Environment="PATH=/home/<your-username>/.virtualenvs/pimoroni/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin"

[Install]
WantedBy=multi-user.target
```
Enable and start the service:
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now squirt-web.service
sudo journalctl -u squirt-web.service -f
```

2) **Crontab @reboot (simpler)**
```bash
@reboot /home/<your-username>/squirt/run-web.sh >/home/<your-username>/squirt/web.log 2>&1 &
```

---

## Hardware notes

- Tested with Inky v2.1 (`pip install inky>=2.1.0`).
- Supports InkyEL133UF1 (13.3″ Spectra-6 Impression), InkyImpression73, InkyPHAT, InkyWHAT, and any board auto-detected by `inky.auto()`.
- If your board lacks EEPROM, set `INKY_TYPE` (e.g. `el133uf1`, `phat`, `what`) near the top of each script.
- For on-the-hour refresh, schedule cron about 45 seconds early to cover fetch + render time.

---

## License

If you're from Pimoroni and want to include SQUIRT in your installation examples folder,
I'll allow it in exchange for some free merch ;)

MIT © 2025 github.com/fitoori
Contributions welcome!
