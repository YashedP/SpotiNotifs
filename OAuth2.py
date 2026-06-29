import os
from dotenv import load_dotenv
from authlib.integrations.requests_client import OAuth2Session
from logging_config import configure_logging, get_logger

load_dotenv()
configure_logging()
logger = get_logger(__name__)

clientId = os.getenv("clientId")
redirectUri = os.getenv("redirectUri")
clientSecret = os.getenv("clientSecret")
authorizationUrl = os.getenv("authorizationUrl")
tokenUrl = os.getenv("tokenUrl")
scope = "user-follow-read playlist-modify-public playlist-modify-private user-read-recently-played playlist-read-private"

sp = OAuth2Session(client_id=clientId, redirect_uri=redirectUri, scope=scope)

def create_authorization_url(state: str) -> str:
    logger.info("Creating Spotify authorization URL", extra={"event": "oauth_authorization_url_started", "state": state})
    authorization_url = sp.create_authorization_url(authorizationUrl, state=state)[0]
    logger.info("Created Spotify authorization URL", extra={"event": "oauth_authorization_url_succeeded", "state": state})
    return authorization_url

def get_access_token(authCode: str) -> dict[str, str]:
    try:
        logger.info("Fetching Spotify access token", extra={"event": "oauth_access_token_started"})
        token = sp.fetch_token(tokenUrl, code=authCode, client_secret=clientSecret)
        logger.info("Fetched Spotify access token", extra={"event": "oauth_access_token_succeeded"})
        return token
    except Exception:
        logger.exception("Failed to fetch Spotify access token", extra={"event": "oauth_access_token_failed"})
        raise

def refresh_access_token(refresh_token: str) -> dict[str, str]:
    try:
        logger.info("Refreshing Spotify access token", extra={"event": "oauth_refresh_token_started"})
        token = sp.refresh_token(tokenUrl, refresh_token=refresh_token, client_secret=clientSecret)
        logger.info("Refreshed Spotify access token", extra={"event": "oauth_refresh_token_succeeded"})
        return token
    except Exception:
        logger.exception("Failed to refresh Spotify access token", extra={"event": "oauth_refresh_token_failed"})
        raise
