"""Trusted operator install/start for the existing account's original host.

No old key, grant, pending request or Source is reused. Transfer encryption
lets the operator set the existing Vercel ingress secret without printing it.
"""
import argparse
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

ROOT = Path('/home/codespace/.deer-platform-service-20261112')
BACKEND = Path('/home/codespace/gateway-r12-restore-yfl4xryg/source/backend')
DATABASE = BACKEND / '.deer-flow/data/deerflow.db'
OWNER = 'oauth:a8ba31a42a7ee885551e6876bbd39951ec417f3c7f5d171474789efc2d35e7d3'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['prepare', 'start'])
    parser.add_argument('--transfer-public-key')
    parser.add_argument('--endpoint')
    args = parser.parse_args()
    source = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(source))
    from fresh_batch_host import FreshBatchHost, canonical, exclusive_write, initialize, private_read
    if args.action == 'prepare':
        # Original database only; never create another runtime store.
        with sqlite3.connect(DATABASE.as_uri() + '?mode=ro', uri=True) as db:
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {'subagent_batches', 'subagent_batch_items'} <= tables:
            raise ValueError('ORIGINAL_BATCH_SCHEMA_REQUIRED')
        subprocess.run(['codex', 'login', 'status'], capture_output=True, check=True)
        from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
        from cryptography.hazmat.primitives.kdf.hkdf import HKDF
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        peer = X25519PublicKey.from_public_bytes(bytes.fromhex(args.transfer_public_key))
        ephemeral = X25519PrivateKey.generate()
        key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None,
                   info=b'deer-platform-ingress-transfer-v1').derive(ephemeral.exchange(peer))
        config = initialize(ROOT, owner=OWNER, platform_service=True, enable_codex=True)
        nonce = os.urandom(12)
        public = canonical(config)
        cipher = AESGCM(key).encrypt(nonce, private_read(ROOT / 'ingress.key'), public)
        bundle = dict(config=config, ephemeral_public=ephemeral.public_key().public_bytes(
            Encoding.Raw, PublicFormat.Raw).hex(), nonce=nonce.hex(), ciphertext=cipher.hex())
        exclusive_write(ROOT / 'transfer.json', canonical(bundle))
        print(json.dumps(bundle, sort_keys=True))
        return
    host = FreshBatchHost(ROOT)
    if host.config['owner'] != OWNER or host.config.get('lifecycle') != 'owner-platform-service-v1':
        raise ValueError('PLATFORM_OWNER_SCOPE_REQUIRED')
    fd = os.open(ROOT / 'supervisor.log', os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'ab') as log:
        proc = subprocess.Popen([sys.executable, str(source / 'platform_service_manager.py'),
            '--root', str(ROOT), '--gateway-backend', str(BACKEND), '--database', str(DATABASE),
            '--endpoint', args.endpoint], stdin=subprocess.DEVNULL, stdout=log, stderr=log,
            start_new_session=True, cwd=BACKEND)
    print(json.dumps(dict(supervisor_pid=proc.pid, generation=host.config['generation'],
                          started=True, readiness='VERIFY_SIGNED_HEARTBEAT')))


if __name__ == '__main__':
    main()
