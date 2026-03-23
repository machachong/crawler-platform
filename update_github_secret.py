import requests
from nacl import encoding, public
import base64
import sys

def encrypt(public_key: str, secret_value: str) -> str:
    """Encrypt a Unicode string using the public key."""
    public_key = public.PublicKey(public_key.encode("utf-8"), encoding.Base64Encoder())
    sealed_box = public.SealedBox(public_key)
    encrypted = sealed_box.encrypt(secret_value.encode("utf-8"))
    return base64.b64encode(encrypted).decode("utf-8")

def update_secret(token, repo, name, value):
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
    }
    # 1. Get public key
    r = requests.get(f"https://api.github.com/repos/{repo}/actions/secrets/public-key", headers=headers)
    r.raise_for_status()
    key_data = r.json()
    
    # 2. Encrypt value
    encrypted_value = encrypt(key_data["key"], value)
    
    # 3. Create or update secret
    data = {
        "encrypted_value": encrypted_value,
        "key_id": key_data["key_id"]
    }
    r = requests.put(f"https://api.github.com/repos/{repo}/actions/secrets/{name}", headers=headers, json=data)
    r.raise_for_status()
    print(f"Successfully updated secret {name}")

if __name__ == "__main__":
    token = sys.argv[1]
    repo = sys.argv[2]
    name = sys.argv[3]
    value = sys.argv[4]
    update_secret(token, repo, name, value)
