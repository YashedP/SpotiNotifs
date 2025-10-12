import os
import requests
from dotenv import load_dotenv
from authlib.integrations.requests_client import OAuth2Session

load_dotenv()

clientId = os.getenv("clientId")
redirectUri = os.getenv("redirectUri")
clientSecret = os.getenv("clientSecret")
authorizationUrl = os.getenv("authorizationUrl")
tokenUrl = os.getenv("tokenUrl")
scope = "user-follow-read playlist-modify-public playlist-modify-private user-read-recently-played playlist-read-private"

sp = OAuth2Session(client_id=clientId, redirect_uri=redirectUri, scope=scope)

def create_authorization_url(state: str) -> str:
    return sp.create_authorization_url(authorizationUrl, state=state)[0]

def get_access_token(authCode: str) -> dict[str, str]:
    token = sp.fetch_token(tokenUrl, code=authCode, client_secret=clientSecret)
    return token

def refresh_access_token(refresh_token: str) -> dict[str, str]:
    data = {
        'grant_type': 'refresh_token',
        'refresh_token': refresh_token,
        'client_id': clientId,
        'client_secret': clientSecret
    }
    
    response = requests.post(tokenUrl, data=data, timeout=10)  # pyright: ignore[reportArgumentType]
    response.raise_for_status()
    
    return response.json()
