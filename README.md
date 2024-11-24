# Govee DreamView T1 Integration for Home Assistant 🏠

[![GitHub Release](https://img.shields.io/github/v/release/necrokote/hass-govee-h6199?sort=semver&style=for-the-badge&color=green)](https://github.com/necrokote/hass-govee-h6199/releases/)
[![GitHub Release Date](https://img.shields.io/github/release-date/necrokote/hass-govee-h6199?style=for-the-badge&color=green)](https://github.com/necrokote/hass-govee-h6199/releases/)
![GitHub Downloads (all assets, latest release)](https://img.shields.io/github/downloads/necrokote/hass-govee-h6199/latest/total?style=for-the-badge&label=Downloads%20latest%20Release)
![GitHub commit activity](https://img.shields.io/github/commit-activity/m/necrokote/hass-govee-h6199?style=for-the-badge)
[![hacs](https://img.shields.io/badge/HACS-Integration-blue.svg?style=for-the-badge)](https://github.com/hacs/integration)

## Overview

The Govee Dreamview T1 Home Assistant Custom Integration allows you to integrate your Govee DreamView T1 (H6199) with your Home Assistant setup via Bluetooth LE.

## Installation

### HACS (recommended)

This integration is available in HACS (Home Assistant Community Store).

1. Install HACS if you don't have it already
2. Open HACS in Home Assistant
3. Go to any of the sections (integrations, frontend, automation).
4. Click on the 3 dots in the top right corner.
5. Select "Custom repositories"
6. Add following URL to the repository `https://github.com/necrokote/hass-govee-h6199`.
7. Select Integration as category.
8. Click the "ADD" button
9. Search for "Govee Bluetooth Lights"
10. Click the "Download" button

### Manual

To install this integration manually you have to download [_govee-ble-lights.zip_](https://github.com/necrokote/hass-govee-h6199/releases/latest/) and extract its contents to `config/custom_components/hass-govee-h6199` directory:

```bash
mkdir -p custom_components/hass-govee-h6199
cd custom_components/hass-govee-h6199
wget https://github.com/necrokote/hass-govee-h6199/releases/latest/download/hass-govee-h6199.zip
unzip hass-govee-h6199.zip
rm hass-govee-h6199.zip
```

## Configuration

### Using UI

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=hass-govee-h6199)

From the Home Assistant front page go to `Configuration` and then select `Devices & Services` from the list.
Use the `Add Integration` button in the bottom right to add a new integration called `Govee Dreamview T1`.

## Help and Contribution

If you find a problem, feel free to report it and I will do my best to help you.
If you have something to contribute, your help is greatly appreciated!
If you want to add a new feature, add a pull request first so we can discuss the details.

## Disclaimer

Use it at your own risk and ensure that you comply with all relevant terms of service and privacy policies.

## Credits
Thanks to https://github.com/egold555/Govee-Reverse-Engineering/blob/master/Products/H6199.md and all the contributors there for reverse-engineering BLE protocol.

Thanks to https://github.com/timniklas/hass-govee-ble-lights/ for the structure and base code for this repo
