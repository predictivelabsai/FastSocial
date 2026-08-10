from fastsocial.security import decrypt_json, decrypt_text, encrypt_json, encrypt_text


def test_credentials_are_encrypted_and_round_trip():
    secret = "token-value-that-must-not-appear-in-storage"
    encrypted = encrypt_text(secret)
    assert encrypted is not None
    assert secret.encode() not in encrypted
    assert decrypt_text(encrypted) == secret

    payload = {"access_token": secret, "scope": ["write"]}
    encrypted_payload = encrypt_json(payload)
    assert secret.encode() not in encrypted_payload
    assert decrypt_json(encrypted_payload) == payload
