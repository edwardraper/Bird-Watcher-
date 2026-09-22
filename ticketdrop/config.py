# Non-secret settings for the ticket-drop notifier. Credentials live in secrets.py.

VENUE_ID = 2783  # Timepiece Exeter on FIXR
VENUE_URL = "https://api.fixr-app.com/api/v2/app/venue/%d" % VENUE_ID
EVENT_URL = "https://fixr.co/event/%s"

POLL_INTERVAL_S = 60
MAX_FAILURES_BEFORE_RESET = 5

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)

WIFI_COUNTRY = "GB"
