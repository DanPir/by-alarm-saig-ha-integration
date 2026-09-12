#!/usr/bin/env python3
"""
decrypt_view_pro_password.py

Decifra il file "associations.json" salvato localmente da View Pro (l'app
Windows per installatori/configuratori Vimar) ed estrae la password di
ogni dispositivo associato all'account - inclusa quella del gateway SAIG
richiesta per l'attach WebSocket locale (vedi ``device_password`` nel
config flow dell'integrazione).

Percorso tipico del file su Windows (varia con la versione di View Pro):
cerca "associations.json" nella cartella dati locale dell'app
(es. sotto %LOCALAPPDATA%) dopo aver effettuato il login in View Pro.

Schema di cifratura (ricostruito da ByMeConfigurator.Aes256CryptoService e
ApplicationUser.CreateRepositoryPassword nel codice decompilato):

  1. password_repo = MD5( parte_locale + useruid + dominio )  in HEX
     MAIUSCOLO, dove l'email e' "parte_locale@dominio"
  2. chiave AES = PBKDF2-HMAC-SHA1( password_repo, salt=8 zero byte,
                                    iterazioni=5000, lunghezza=32 )
  3. contenuto file = base64( IV (16 byte) + AES-256-CBC-PKCS7(JSON) )
  4. il plaintext decifrato e' JSON codificato UTF-16-LE

Lo User UID va ottenuto PRIMA di usare questo script (serve per calcolare
la chiave): e' il claim "sub" del token JWT del tuo account Vimar,
leggibile intercettando il traffico di login di un'app Vimar (View Pro o
l'app mobile View - vedi il README per i dettagli).

Uso:
    pip install cryptography
    python decrypt_view_pro_password.py associations.json \
        --email tuo@account.com --useruid <uid-dal-jwt>
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC


def create_repository_password(email: str, useruid: str) -> str:
    """Ricostruisce ApplicationUser.CreateRepositoryPassword."""
    parts = email.split("@")
    if len(parts) > 1:
        combined = parts[0] + useruid + parts[1]
    else:
        combined = email + useruid
    return hashlib.md5(combined.encode("utf-8")).hexdigest().upper()


def derive_key(repository_password: str) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA1(),
        length=32,
        salt=b"\x00" * 8,
        iterations=5000,
    )
    return kdf.derive(repository_password.encode("utf-8"))


def decrypt_file(path: str, key: bytes) -> dict:
    with open(path, encoding="utf-8") as f:
        blob = base64.b64decode(f.read().strip())

    iv, ciphertext = blob[:16], blob[16:]
    decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    plaintext = decryptor.update(ciphertext) + decryptor.finalize()

    pad_len = plaintext[-1]
    if 1 <= pad_len <= 16:
        plaintext = plaintext[:-pad_len]

    text = plaintext.decode("utf-16-le", errors="replace")
    text = text[text.find("{"):]
    try:
        return json.loads(text)
    except json.JSONDecodeError as err:
        # A volte restano byte spuri dopo la fine del JSON: tronca al punto
        # in cui il parser si e' fermato e riprova.
        return json.loads(text[: err.pos])


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Decifra associations.json di View Pro ed estrae le password dei dispositivi associati."
    )
    parser.add_argument("path", help="Percorso del file associations.json")
    parser.add_argument("--email", required=True, help="Email del tuo account Vimar")
    parser.add_argument(
        "--useruid", required=True, help="User UID (claim 'sub' del token JWT dell'account)"
    )
    args = parser.parse_args()

    repository_password = create_repository_password(args.email, args.useruid)
    key = derive_key(repository_password)
    associations = decrypt_file(args.path, key)

    print(json.dumps(associations, indent=2))
    print("\n===== PASSWORD DISPOSITIVI =====")
    data = associations.get("data", associations)
    for duid, info in data.items():
        password = info.get("password", "?")
        print(f"  {duid}: {password}")


if __name__ == "__main__":
    main()
