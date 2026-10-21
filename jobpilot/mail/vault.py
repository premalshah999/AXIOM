"""Sealing mailbox credentials at rest."""

from cryptography.fernet import Fernet
import json
import os


class Vault:
    def __init__(self, config):
        path = config.data_dir / "mail-key"
        if config.mail_encryption_key:
            key = config.mail_encryption_key.encode()
        else:
            try:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                pass
            else:
                with os.fdopen(fd, "wb") as f:
                    f.write(Fernet.generate_key())
            key = path.read_bytes().strip()
        self.cipher = Fernet(key)

    def seal(self, value):
        return self.cipher.encrypt(json.dumps(value).encode()).decode()

    def open(self, value):
        return json.loads(self.cipher.decrypt(value.encode()))
