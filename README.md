# MirAIe Local

A Home Assistant custom integration for **local (LAN, UDP) control** of a Panasonic
MirAIe Wi-Fi air conditioner.

This integration talks to the air conditioner **entirely over the local network**. There is
no cloud dependency at runtime: once set up, the unit is controlled by sending and receiving
UDP packets on your LAN.

## What it is

- A Home Assistant `custom_components` integration (`domain: miraie_local`).
- Local-first: no vendor cloud account or internet connection is required at runtime.
- Installable via HACS as a **custom repository**.

The local UDP protocol used by the unit was **reverse engineered from the device firmware**.
It is not an officially documented or vendor-supported interface, so it may change between
firmware versions and is provided as-is.

## Install with HACS

1. Make sure [HACS](https://hacs.xyz/) is installed.
2. In Home Assistant, open **HACS → Integrations**.
3. Open the three-dot menu (top right) and choose **Custom repositories**.
4. Add `https://github.com/chetasr/ha-miraie-local` with category **Integration**.
5. Search for **MirAIe Local** in HACS and download it.
6. Restart Home Assistant.

## Setup

1. Go to **Settings → Devices & Services → Add Integration**.
2. Search for **MirAIe Local** and select it.
3. Enter the connection details for your air conditioner when prompted.
4. The unit is discovered and controlled over the local network.

## Supported entities

- **Climate** — the air conditioner (power, mode, target temperature, fan speed).
- **Diagnostic sensors** — read-only sensors exposing device/diagnostic information.

## Notes

- The local UDP protocol was reverse engineered from the device firmware. It is not
  officially documented by Panasonic and is not supported by the vendor.
- This project is not affiliated with or endorsed by Panasonic.
