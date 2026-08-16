"""Constants for Volvo (AU) integration."""

DOMAIN = "volvo_au"

# Config entry keys
CONF_VIN = "vin"
CONF_DPOP_PRIVATE_KEY_PEM = "dpop_private_key_pem"
CONF_REFRESH_TOKEN = "refresh_token"
CONF_ID_TOKEN = "id_token"
CONF_APP_INSTALLATION_ID = "app_installation_id"
CONF_MODEL_NAME = "model_name"
CONF_MODEL_YEAR = "model_year"
CONF_REGISTRATION_PLATE = "registration_plate"

# Cadence (seconds)
POLL_IDLE = 300       # 5 min when the car is dormant
POLL_ACTIVE = 60      # 1 min when "interesting" (charging, doors open, recent command)
POLL_POST_CMD_FAST = 5  # 5 s polls for the first minute after a command
POLL_POST_CMD_WINDOW = 60

# Volvo OAuth / API
ISSUER = "https://volvoid.eu.volvocars.com"
TOKEN_URL = f"{ISSUER}/as/token.oauth2"
AUTHORIZE_URL = f"{ISSUER}/as/authorization.oauth2"
CLIENT_ID = "nxpwptn_10"
REDIRECT_URI = "volvooncall://auth/callback"
USER_AGENT = "vca-ios/6.9.0"

SCOPES = (
    "openid email profile phone address "
    "care_by_volvo:financial_information:invoice:read "
    "care_by_volvo:financial_information:payment_method "
    "care_by_volvo:subscription:read "
    "customer:attributes customer:attributes:write "
    "vehicle:attributes order:attributes "
    "account_link:link:admin:read "
    "volvo_on_call:all"
)
ACR_VALUES = "urn:volvoid:aal:bronze:2sv"
UI_LOCALES = "en-GB"

API_BASE = "https://cepmobtoken.kr.prod.c3.volvocars.com"
DEFAULT_APP_INSTALLATION_ID = "2EE3A7A9-870F-4B74-BA09-E6BFB713CDA7"
