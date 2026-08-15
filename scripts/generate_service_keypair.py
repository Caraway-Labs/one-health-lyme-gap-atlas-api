"""Generate an encrypted local key pair for the API Snowflake service user."""

from __future__ import annotations

import argparse
import getpass
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

PRIVATE_FILE_NAME = "oh_lyme_api_2026.p8"
PUBLIC_FILE_NAME = "oh_lyme_api_2026.pub"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-directory",
        required=True,
        type=Path,
        help="Existing or new directory outside this repository for the key files.",
    )
    args = parser.parse_args()
    output_directory: Path = args.output_directory.expanduser().resolve()
    output_directory.mkdir(parents=True, exist_ok=True)

    private_path = output_directory / PRIVATE_FILE_NAME
    public_path = output_directory / PUBLIC_FILE_NAME
    if private_path.exists() or public_path.exists():
        raise FileExistsError(
            "Refusing to overwrite an existing key file. "
            "Choose a new directory or rotate explicitly."
        )

    passphrase = getpass.getpass("Enter a new private-key passphrase: ").encode()
    confirmation = getpass.getpass("Confirm the private-key passphrase: ").encode()
    if len(passphrase) < 16:
        raise ValueError("Use a passphrase of at least 16 characters.")
    if passphrase != confirmation:
        raise ValueError("Passphrases did not match.")

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_path.write_bytes(
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.BestAvailableEncryption(passphrase),
        )
    )
    public_path.write_bytes(
        private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )

    print(f"Encrypted private key: {private_path}")
    print(f"Public key: {public_path}")
    print("Keep the private key and passphrase out of source control and chat.")


if __name__ == "__main__":
    main()
