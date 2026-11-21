"""Read-only Google Drive fetch for the MEGA 知識庫 importer. Default off.

The Owner creates a Google service account with only the Drive readonly scope,
shares the one source folder with that account as Viewer, and sets:

  XJ_DRIVE_SA_JSON              Sensitive: service-account key JSON
  XJ_DRIVE_ALLOWED_FOLDER_IDS   comma-separated folder ids; every parent of
                                the file must be in this set

No token, key, URL or link is logged. HTTP goes only to Google's token and
Drive hosts, with proxies disabled and redirects confined to those hosts.
The bearer token is removed when a download redirects off www.googleapis.com.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from mega_library import MAX_READ_BYTES, MegaDenied

TOKEN_URL = 'https://oauth2.googleapis.com/token'
DRIVE_API = 'https://www.googleapis.com/drive/v3/files/'
READONLY_SCOPE = 'https://www.googleapis.com/auth/drive.readonly'
DRIVE_ID = re.compile(r'[A-Za-z0-9_-]{10,128}\Z')
REVISION_ID = re.compile(r'[A-Za-z0-9_-]{1,200}\Z')
SA_EMAIL = re.compile(r'[a-z0-9-]{1,64}@[a-z0-9-]{1,64}\.iam\.gserviceaccount\.com\Z')
_CODE = re.compile(r'[A-Z][A-Z0-9_]{0,80}\Z')


def _host_ok(url):
    host = (urllib.parse.urlsplit(url).hostname or '').lower()
    return host == 'www.googleapis.com' or host.endswith('.googleusercontent.com')


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise MegaDenied('DRIVE_AUTH_FAILED')


class _GoogleRedirect(urllib.request.HTTPRedirectHandler):
    """Follow only Google hosts. Drop Authorization when the host changes."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _host_ok(newurl):
            raise MegaDenied('DRIVE_UNREACHABLE')
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is None:
            return None
        old = (urllib.parse.urlsplit(req.full_url).hostname or '').lower()
        nxt = (urllib.parse.urlsplit(newurl).hostname or '').lower()
        if old != nxt:
            new.remove_header('Authorization')
        return new


def _opener(redirect):
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), redirect)


def _post_form(url, data):
    if url != TOKEN_URL:
        raise MegaDenied('DRIVE_AUTH_FAILED')
    req = urllib.request.Request(
        url, data=data, method='POST',
        headers={'Content-Type': 'application/x-www-form-urlencoded',
                 'Accept': 'application/json'})
    try:
        with _opener(_NoRedirect).open(req, timeout=20) as resp:
            payload = json.loads(resp.read(65536).decode('utf-8'))
    except MegaDenied:
        raise
    except Exception:
        raise MegaDenied('DRIVE_AUTH_FAILED') from None
    if not isinstance(payload, dict):
        raise MegaDenied('DRIVE_AUTH_FAILED')
    return payload


def _open_get(url, headers):
    if not url.startswith(DRIVE_API):
        raise MegaDenied('DRIVE_UNREACHABLE')
    req = urllib.request.Request(url, headers=headers, method='GET')
    try:
        return _opener(_GoogleRedirect).open(req, timeout=30)
    except MegaDenied:
        raise
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise MegaDenied('DRIVE_AUTH_FAILED') from None
        if exc.code == 404:
            raise MegaDenied('DRIVE_FILE_NOT_FOUND') from None
        raise MegaDenied('DRIVE_UNREACHABLE') from None
    except Exception:
        raise MegaDenied('DRIVE_UNREACHABLE') from None


def parse_service_account(raw):
    """Return {client_email, private_key} or raise a fixed code. Never echoes raw."""
    try:
        info = json.loads(raw) if isinstance(raw, str) else None
    except ValueError:
        info = None
    if not isinstance(info, dict):
        raise MegaDenied('DRIVE_SA_INVALID')
    email, key = info.get('client_email'), info.get('private_key')
    token_uri = info.get('token_uri', TOKEN_URL)
    if (info.get('type') != 'service_account'
            or not isinstance(email, str) or SA_EMAIL.fullmatch(email) is None
            or token_uri != TOKEN_URL
            or not isinstance(key, str) or 'PRIVATE KEY' not in key
            or len(key) > 20000 or '\n' not in key):
        raise MegaDenied('DRIVE_SA_INVALID')
    return {'client_email': email, 'private_key': key}


def parse_allowlist(raw):
    if not isinstance(raw, str) or not raw.strip():
        raise MegaDenied('DRIVE_ALLOWLIST_INVALID')
    parts = [part.strip() for part in raw.split(',')]
    if not parts or any(DRIVE_ID.fullmatch(part) is None for part in parts):
        raise MegaDenied('DRIVE_ALLOWLIST_INVALID')
    return frozenset(parts)


def parse_cap(raw):
    """Bytes, default and maximum are the reader's 25 MiB cap."""
    if raw is None or raw == '':
        return MAX_READ_BYTES
    if not isinstance(raw, str) or re.fullmatch(r'[1-9][0-9]{0,8}', raw) is None:
        raise MegaDenied('DRIVE_CAP_INVALID')
    size = int(raw)
    if size < 1 or size > MAX_READ_BYTES:
        raise MegaDenied('DRIVE_CAP_INVALID')
    return size


def _file_url(file_id, params):
    if DRIVE_ID.fullmatch(file_id) is None:
        raise MegaDenied('DRIVE_ID_INVALID')
    return DRIVE_API + file_id + '?' + urllib.parse.urlencode(params)


class DriveClient:
    """One service account. Holds the key in memory; nothing here is logged."""

    def __init__(self, account, allowlist, post_form=_post_form, open_get=_open_get,
                 clock=time.time):
        self._email = account['client_email']
        self._key = account['private_key']
        self.allowlist = frozenset(allowlist)
        self._post_form, self._open, self._clock = post_form, open_get, clock
        self._token, self._token_exp = None, 0.0

    def _access_token(self):
        now = self._clock()
        if self._token and now < self._token_exp:
            return self._token
        import jwt
        try:
            assertion = jwt.encode({
                'iss': self._email,
                'scope': READONLY_SCOPE,
                'aud': TOKEN_URL,
                'iat': int(now),
                'exp': int(now) + 3600,
            }, self._key, algorithm='RS256')
            body = urllib.parse.urlencode({
                'grant_type': 'urn:ietf:params:oauth:grant-type:jwt-bearer',
                'assertion': assertion,
            }).encode()
            payload = self._post_form(TOKEN_URL, body)
        except MegaDenied:
            raise
        except Exception:
            raise MegaDenied('DRIVE_AUTH_FAILED') from None
        token = payload.get('access_token') if isinstance(payload, dict) else None
        if (not isinstance(token, str) or not 1 <= len(token) <= 4096
                or any(ch.isspace() for ch in token)):
            raise MegaDenied('DRIVE_AUTH_FAILED')
        try:
            ttl = int(payload.get('expires_in') or 3600)
        except (TypeError, ValueError):
            ttl = 3600
        self._token = token
        self._token_exp = now + max(60, min(ttl, 3600) - 60)
        return token

    def _get_json(self, url):
        try:
            with self._open(url, {'Authorization': 'Bearer ' + self._access_token(),
                                  'Accept': 'application/json'}) as resp:
                data = json.loads(resp.read(1 << 20).decode('utf-8'))
        except MegaDenied:
            raise
        except Exception:
            raise MegaDenied('DRIVE_UNREACHABLE') from None
        if not isinstance(data, dict):
            raise MegaDenied('DRIVE_METADATA_INVALID')
        return data

    def metadata(self, file_id):
        """Size, parents, name and headRevisionId. Parents must all be allowlisted."""
        raw = self._get_json(_file_url(file_id, {
            'fields': 'id,name,size,parents,headRevisionId,mimeType',
            'supportsAllDrives': 'true'}))
        if raw.get('id') != file_id:
            raise MegaDenied('DRIVE_METADATA_INVALID')
        mime = raw.get('mimeType')
        if not isinstance(mime, str) or mime.startswith('application/vnd.google-apps.'):
            raise MegaDenied('DRIVE_NOT_DOWNLOADABLE')
        size_raw = raw.get('size')
        if not isinstance(size_raw, str) or re.fullmatch(r'[0-9]{1,10}', size_raw) is None:
            raise MegaDenied('DRIVE_NOT_DOWNLOADABLE')
        revision = raw.get('headRevisionId')
        if not isinstance(revision, str) or REVISION_ID.fullmatch(revision) is None:
            raise MegaDenied('DRIVE_METADATA_INVALID')
        name = raw.get('name')
        if not isinstance(name, str):
            raise MegaDenied('DRIVE_NAME_INVALID')
        parents = raw.get('parents')
        if (not isinstance(parents, list) or not 1 <= len(parents) <= 20
                or any(type(parent) is not str or DRIVE_ID.fullmatch(parent) is None
                       or parent not in self.allowlist for parent in parents)):
            raise MegaDenied('DRIVE_PARENT_NOT_ALLOWED')
        return {'id': file_id, 'name': name, 'size': int(size_raw),
                'parents': list(parents), 'headRevisionId': revision, 'mimeType': mime}

    def download(self, file_id, expected_size):
        """Stream the bytes and return (data, sha256 hex). Stops past expected_size."""
        import hashlib
        url = _file_url(file_id, {'alt': 'media', 'supportsAllDrives': 'true'})
        digest, buf = hashlib.sha256(), bytearray()
        try:
            with self._open(url, {'Authorization': 'Bearer ' + self._access_token(),
                                  'Accept': 'application/octet-stream'}) as resp:
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    if len(buf) + len(chunk) > expected_size:
                        raise MegaDenied('DRIVE_SIZE_MISMATCH')
                    buf += chunk
                    digest.update(chunk)
        except MegaDenied:
            raise
        except Exception:
            raise MegaDenied('DRIVE_DOWNLOAD_FAILED') from None
        if len(buf) != expected_size:
            raise MegaDenied('DRIVE_SIZE_MISMATCH')
        return bytes(buf), digest.hexdigest()


def safe_code(exc):
    text = str(exc) if isinstance(exc, MegaDenied) else 'MEGA_INTERNAL_' + type(exc).__name__
    return text if _CODE.fullmatch(text) else 'MEGA_INTERNAL_ERROR'
