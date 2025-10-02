import os
from dotenv import load_dotenv
from authlib.integrations.requests_client import OAuth2Session
import requests
import signal
import time

load_dotenv()

clientId = os.getenv("clientId")
redirectUri = os.getenv("redirectUri")
clientSecret = os.getenv("clientSecret")
authorizationUrl = os.getenv("authorizationUrl")
tokenUrl = os.getenv("tokenUrl")
scope = "user-follow-read playlist-modify-public playlist-modify-private user-read-recently-played playlist-read-private"

# Create OAuth2Session with timeout configuration
sp = OAuth2Session(
    client_id=clientId, 
    redirect_uri=redirectUri, 
    scope=scope,
    timeout=30  # 30 second timeout
)

def create_authorization_url(state: str) -> str:
    return sp.create_authorization_url(authorizationUrl, state=state)[0]

def get_access_token(authCode: str) -> dict[str, str]:
    print(f"DEBUG - OAuth2.get_access_token called with authCode: {authCode}")
    print(f"DEBUG - tokenUrl: {tokenUrl}")
    print(f"DEBUG - clientId: {'✓' if clientId else '✗'}")
    print(f"DEBUG - clientSecret: {'✓' if clientSecret else '✗'}")
    print(f"DEBUG - redirectUri: {redirectUri}")
    print(f"DEBUG - About to call sp.fetch_token...")
    
    # Check if any required values are None
    if not clientId:
        raise ValueError("clientId is None - check .env file")
    if not clientSecret:
        raise ValueError("clientSecret is None - check .env file")
    if not tokenUrl:
        raise ValueError("tokenUrl is None - check .env file")
    
    try:
        print(f"DEBUG - Making request to: {tokenUrl}")
        print(f"DEBUG - Request will timeout after 30 seconds...")
        
        # Set a timeout for the request
        start_time = time.time()
        token = sp.fetch_token(tokenUrl, code=authCode, client_secret=clientSecret)
        end_time = time.time()
        
        print(f"DEBUG - Request completed in {end_time - start_time:.2f} seconds")
        print(f"DEBUG - Successfully got token: {list(token.keys())}")
        return token
    except requests.exceptions.Timeout:
        print("ERROR - Request timed out after 30 seconds")
        raise
    except requests.exceptions.ConnectionError as e:
        print(f"ERROR - Connection error: {e}")
        raise
    except Exception as e:
        print(f"ERROR - Failed to fetch token: {e}")
        print(f"ERROR - Exception type: {type(e)}")
        print(f"ERROR - Exception details: {str(e)}")
        raise

def refresh_access_token(refresh_token: str) -> dict[str, str]:
    token = sp.refresh_token(tokenUrl, refresh_token=refresh_token, client_secret=clientSecret)
    return token
