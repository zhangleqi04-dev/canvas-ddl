def bearer_headers(token: str) -> dict[str, str]:
    """Infrastructure-only headers; never serialize or log this value."""
    return {"Authorization": f"Bearer {token}", "Accept": "application/json"}

