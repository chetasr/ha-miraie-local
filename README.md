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
3. Sign in with your MirAIe account (mobile number and password). The
   integration reads the unit's device ID, encryption key and multicast group from
   the account and then finds the air conditioner on your network itself.
4. If the search fails, enter the unit's IP address when asked. There is also an
   advanced manual path if you would rather enter everything yourself.

The account is only used during setup (and if the key is ever rotated). After that
the integration talks to the unit over the LAN only, and the cloud is not contacted.

## Supported entities

- **Climate** — the air conditioner (power, mode, target temperature, fan speed).
- **Diagnostic sensors** — signal strength, room temperature and firmware version.

Setting a temperature, mode or fan speed also powers the unit on, because setting a
value on a unit that is off would otherwise do nothing.

## Options

The air conditioner only reports its state when something changes, so the integration
nudges it after a period with no report (60 seconds by default, minimum 15). The
nudge never carries a command: it asks the unit to repeat its state. Every report
resets the timer, so while the unit is reporting nothing extra is sent.

## How the local protocol works

The unit speaks an encrypted UDP protocol that was reverse engineered from its
firmware. The pieces are deliberately separated so each can be tested alone:

| Module | Role |
| --- | --- |
| `crypto.py` | AES-256-CBC with zero padding (pure) |
| `protocol.py` | frame codec and command map (pure) |
| `transport.py` | the only module that touches a socket |
| `coordinator.py` | pushes status to the entities |
| `climate.py`, `sensor.py` | the entities |

Each frame is an 18-byte cleartext header (a direction byte that must be `0x01`
for a command, then a big-endian length) followed by the encrypted JSON body. The
unit broadcasts its state to `255.255.255.255:5002` whenever it changes.

## Diagnostics

Download a diagnostics dump from the integration page. The encryption key,
initialisation vector, password and mobile number are redacted, because anyone on the
LAN holding the key can control the unit.

## Notes

- The local UDP protocol was reverse engineered from the device firmware. It is not
  officially documented by Panasonic and is not supported by the vendor, so it may
  change between firmware versions and is provided as-is.
- This project is not affiliated with or endorsed by Panasonic.
