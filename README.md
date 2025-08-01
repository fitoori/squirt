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
    *The first line will refresh the display once per hour to a display a new xkcd comic. The second line runs status.py message on boot, leaves it for ~30 seconds, then switches to showing NASA's Astronomy Photo of the Day (apod).*

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
- If you intend on having the display refresh *on the hour* set your crontab to run ~45 seconds before the hour changes - that offsets the time it takes to fetch, render, push to display, and start the refresh process. 

---

## License

If you're from Pimoroni and want to include SQUIRT in your installation examples folder,
I'll allow it in exchange for some free merch ;)

MIT © 2025 github.com/fitoori  
Contributions welcome!

