DOMAIN = "miraie_local"
DEFAULT_PORT = 5001
BROADCAST_PORT = 5002
DEFAULT_MULTICAST = "239.255.0.15"
MANUFACTURER = "Panasonic"
MODEL = "MirAIe AC"

# Config entry keys. ``host`` is auto-discovered from the DISC_REQ reply but can be
# overridden; the rest come from the MirAIe cloud.
CONF_HOST = "host"
CONF_DEVICE_ID = "device_id"
CONF_KEY = "key"
CONF_IV = "iv"
CONF_MULTICAST_IP = "multicast_ip"
CONF_MOBILE = "mobile"
CONF_POLL_INTERVAL = "poll_interval"

# The nudge is only sent after this many seconds of SILENCE - every status
# broadcast resets it - so it is not a heartbeat (decision D5).
DEFAULT_POLL_INTERVAL = 60
MIN_POLL_INTERVAL = 15
