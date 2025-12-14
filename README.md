# SQUIRT
_Spectra-Qualified Uncomplicated Inky Rendering Tools_

A set of one-shot Python 3 scripts for Raspberry Pi (or any Linux SBC)  
that displays content on an Inky e-paper panel  
(13.3″ Spectra-6 Impression, 7-colour Impression 7.3″, pHAT, wHAT, or any board detected by `inky.auto`).

---

## Features

- **One-shot design**  
  Run once, display one image, exit cleanly (ideal for cron or systemd).
- **Auto-install**  
  Installs `inky` & `numpy` under your Python interpreter if missing.
- **Headless fallback**  
  Generates a `*_preview.png` when no Inky hardware is found.
- **Consistent helpers**  
  Shared functions for HTTP, image-fitting, Inky detection, and CLI parsing.
- **Self-Growing Offline Functionality**
  All fetched images are archived.
  If the scripts are called without an internet connection, they cycle saved content. 

---

## Installation

### One-liner (automated)

1. Clone the repo and run the installer as root (or via sudo):
   ```bash
   git clone https://github.com/fitoori/squirt.git
   cd squirt
   sudo ./install.sh --user "$USER"
   ```

   The installer will:
   - Install apt prerequisites
   - Copy SQUIRT into `/opt/squirt` and symlink it to `~/squirt`
   - Create a dedicated Python virtualenv and install `requirements.txt`
   - Enable a `squirt-web.service` systemd unit for the web UI
   - Add safe, idempotent crontab entries for hourly XKCD + boot-time APOD
   - Drop a small `/etc/motd.d/10-squirt` hint with the above locations

### Manual steps

1. **Install apt dependencies**
    ```bash
    sudo apt update && sudo apt install -y \
      git \
      python3-pip python3-setuptools python3-wheel python3-venv \
      python3-numpy python3-pil python3-spidev \
      python3-rpi.gpio python3-libgpiod \
      python3-smbus2 python3-lxml
    ```

2. **Install Inky Libraries**  
   Follow the [official Pimoroni Inky instructions](https://github.com/pimoroni/inky).

3. **Enable I2C and SPI**
    ```bash
    sudo raspi-config nonint do_i2c 0 do_spi 0
    ```

4. **Clone this repo and make scripts executable**
    ```bash
    git clone https://github.com/fitoori/squirt.git
    cd squirt
    sudo chmod +x xkcd.py nasa.py landscapes.py save.py status.py
    ```

5. **Enable the virtual environment created by Pimoroni’s inky script**
    ```bash
    source ~/.virtualenvs/pimoroni/bin/activate
    ```

6. **Install pip dependencies (if anything was missed)**
    ```bash
    pip3 install inky numpy requests beautifulsoup4 pillow
    ```
7. **(Optional) Edit Crontab**
    ```bash
    crontab -e
    ```
    Scroll down to the bottom, paste in the following:
    ```bash
    0 * * * * python3 /home/$USER/squirt/xkcd.py
    @reboot python3 /home/$USER/squirt/status.py && sleep 30 && python3 /home/$USER/squirt/nasa.py --apod &
    ```
    *The first line will refresh the display once per hour to a display a new xkcd comic. The second line runs status.py message on boot, leaves it for ~30 seconds, then switches to showing NASA's[...]

---

## Run the web UI at boot

If you use the optional web UI included in this repository (e.g. a script named `web.py`, `app.py`, or similar that starts a Flask/Quart/FastAPI server), you can run it automatically on system boot. There are two recommended approaches: a systemd service (preferred) or a crontab @reboot entry.

1) Recommended — systemd service (clean, restarts on failure)

- Create a small wrapper script that activates your virtualenv and starts the web UI. Replace <your-username> and <script-path> below.

```bash
# /home/<your-username>/squirt/run-web.sh
#!/bin/bash
set -e
# activate pimoroni virtualenv if used
source /home/<your-username>/.virtualenvs/pimoroni/bin/activate || true
# cd to repo and run the web UI (replace web.py with your UI entrypoint)
cd /home/<your-username>/squirt
exec python3 web.py
```

Make it executable:
```bash
chmod +x /home/<your-username>/squirt/run-web.sh
```

- Create a systemd unit file /etc/systemd/system/squirt-web.service (requires sudo). Replace User= with the account that should run the UI (for example, pi or your username).

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

Then enable and start the service:
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now squirt-web.service
sudo journalctl -u squirt-web.service -f
```

Notes:
- If your web UI binds to port <1024> you must run it as root or use a reverse proxy. Prefer binding to a high port (e.g. 8000) and reverse-proxy with nginx if needed.
- The service file above sets PATH so the pimoroni virtualenv python/pip are preferred. Adjust Environment or ExecStart if you use a different venv.

2) Simpler — crontab @reboot

Add a single @reboot line to your crontab that runs the wrapper script in the background. This is less robust than systemd (no automatic restart on failure) but quick to set up:

```bash
crontab -e
# add a line (replace <your-username> and the entrypoint if needed)
@reboot /home/<your-username>/squirt/run-web.sh >/home/<your-username>/squirt/web.log 2>&1 &
```

This will start the web UI after the system boots and write logs to web.log.

---

## Usage

- **Fetch and display a random XKCD comic:**
    ```bash
    python3 ./xkcd.py
    ```

- **Fetch and display a NASA image:**
    ```bash
    python3 ./nasa.py [--apod|--mars [ROVER]|--epic|--earth LAT LON [--dim]|--search "QUERY"] [--key API_KEY] [--landscape|--portrait]
    ```

- **Fetch and display an unseen landscape painting:**
    ```bash
    python3 ./landscapes.py [--met|--aic|--cma] [--wide|--tall] [--reset]
    ```

- **Fetch and display an image from a URL, or cycle through saved images:**
    ```bash
    python3 ./save.py [URL] [--folder DIR] [--reset]
    ```
- **Display system status message:**
    ```bash
    python3 ./status.py [--force-triangle] [--no-pisugar]
    ```
*Note: Contrary to indication, the screen will not automatically refresh if this script is manually called, it is meant to be used in conjunction with other scripts + crontab.*

---

## Hardware Notes

- Tested with Inky v2.1 (`pip install inky>=2.1.0`)
- Supports:
  - InkyEL133UF1 (13.3″ Spectra-6 Impression)
  - InkyImpression73 (7-colour 7.3″ Impression)
  - InkyPHAT, InkyWHAT
  - Any board auto-detected by `inky.auto()`
- If your board lacks EEPROM, set `INKY_TYPE` (e.g. `el133uf1`, `phat`, `what`) near the top of each script.
- If you intend on having the display refresh *on the hour* set your crontab to run ~45 seconds before the hour changes - that offsets the time it takes to fetch, render, push to display, and sta[...] 

---

## License

If you're from Pimoroni and want to include SQUIRT in your installation examples folder,
I'll allow it in exchange for some free merch ;)

MIT © 2025 github.com/fitoori  
Contributions welcome!