"""Production native transport with durable single-submission intents.

Receipts prove hub state only, never independent recipient rendering.
No login, profile takeover, automatic retries or GUI fallback.
"""
import hashlib
import json
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request


class TransportError(RuntimeError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise TransportError('redirect_refused')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':')).encode()).hexdigest()


class ProtocolTransport:
    def __init__(self, endpoint, token, server_id, account, room, journal):
        url = urllib.parse.urlsplit(endpoint)
        if (url.scheme != 'http' or url.hostname not in ('127.0.0.1', '::1')
                or url.username or url.password or url.query or url.fragment
                or url.path not in ('', '/') or not url.port):
            raise TransportError('loopback_endpoint_required')
        if not all(isinstance(v, str) and v for v in (token, server_id, account, room)):
            raise TransportError('scope_required')
        self.endpoint, self.token = endpoint.rstrip('/'), token
        self.server_id, self.account, self.room = server_id, account, room
        self.base = '/v1/accounts/' + urllib.parse.quote(account, safe='')
        self.http = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        self.db = sqlite3.connect(journal, timeout=10)
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('CREATE TABLE IF NOT EXISTS intents (job TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, state TEXT NOT NULL)')
        self.db.commit()

    def close(self):
        self.db.close()

    def request(self, path, body=None, key=None):
        headers = {'Authorization': 'Bearer ' + self.token}
        if key:
            headers['Idempotency-Key'] = key
        data = None
        if body is not None:
            headers['Content-Type'] = 'application/json'
            data = json.dumps(body).encode()
        req = urllib.request.Request(self.endpoint + path, data=data, headers=headers)
        try:
            with self.http.open(req, timeout=8) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            # Never include response text, URLs or credentials in diagnostics.
            raise TransportError('http_' + str(exc.code)) from None
        except (OSError, ValueError):
            raise TransportError('transport_unavailable') from None

    def message(self, mid):
        return self.request(self.base + '/messages/' + urllib.parse.quote(mid, safe=''))

    def deliver(self, job, timeout=20):
        required = ('id', 'account', 'room', 'source_id', 'source_server_id', 'sender', 'answer')
        if any(not isinstance(job.get(k), str) or not job[k] for k in required):
            raise TransportError('invalid_job')
        if (job['account'], job['room']) != (self.account, self.room):
            raise TransportError('scope_mismatch')
        if self.request('/v1/status').get('serverId') != self.server_id:
            raise TransportError('server_mismatch')
        identity = [self.server_id, self.account, self.room, job['id']]
        key = 'candidate-' + digest(identity)
        fingerprint = digest({**job, 'server': self.server_id})
        # Commit the one allowed submission before any network mutation. A crash
        # between commit and POST deliberately requires review, not a blind retry.
        self.db.execute('BEGIN IMMEDIATE')
        previous = self.db.execute('SELECT fingerprint,state FROM intents WHERE job=?', (key,)).fetchone()
        if previous:
            self.db.rollback()
            if previous[0] != fingerprint:
                raise TransportError('job_changed')
        else:
            self.db.rollback()
            source = self.message(job['source_id'])
            if (source.get('id'), source.get('accountId'), source.get('threadId'),
                source.get('serverId'), source.get('senderId')) != (
                    job['source_id'], self.account, self.room, job['source_server_id'], job['sender']):
                raise TransportError('source_mismatch')
            if source.get('recall'):
                raise TransportError('source_recalled')
            self.db.execute('BEGIN IMMEDIATE')
            inserted = self.db.execute('INSERT OR IGNORE INTO intents VALUES (?,?,?)',
                                      (key, fingerprint, 'submission_started')).rowcount
            self.db.commit()
            if not inserted:
                return self.deliver(job, timeout)
            try:
                self.request(self.base + '/sends', {'kind': 'text', 'threadId': self.room,
                    'text': job['answer'], 'quote': {'messageId': job['source_id'],
                    'serverId': job['source_server_id']}}, key)
            except TransportError:
                # Even a missing HTTP response may follow server acceptance.
                pass
        deadline = time.monotonic() + timeout
        while True:
            try:
                intent = self.request(self.base + '/sends/by-idempotency-key?key=' + key)
                if intent.get('accountId') != self.account or intent.get('destination') != self.room:
                    raise TransportError('receipt_scope_mismatch')
                state = intent.get('state')
                if state == 'accepted' and intent.get('localMessageId'):
                    message = self.message(intent['localMessageId'])
                    if (message.get('id'), message.get('accountId'), message.get('threadId'),
                        message.get('serverId'), message.get('direction'), message.get('body'),
                        message.get('type'), message.get('appMessageType'),
                        (message.get('quote') or {}).get('serverId')) != (
                            intent['localMessageId'], self.account, self.room, intent.get('serverId'),
                            'outgoing', job['answer'], 49, 57, job['source_server_id']) or not intent.get('serverId'):
                        raise TransportError('receipt_mismatch')
                    self.db.execute('UPDATE intents SET state=? WHERE job=?', ('hub_verified', key))
                    self.db.commit()
                    return {'state': 'hub_verified', 'intent_id': intent['id'],
                            'server_id': intent['serverId'], 'recipient_verified': False}
                if state in ('needs_review', 'failed', 'cancelled'):
                    self.db.execute('UPDATE intents SET state=? WHERE job=?', (state, key))
                    self.db.commit()
                    code=intent.get('errorCode')
                    return {'state': state, 'recipient_verified': False,
                            'error_code':code if code in ('transport_before_submission','service_rejected','send_admission_revoked','quote_target_unavailable') else None}
            except TransportError as exc:
                if str(exc) not in ('http_404', 'transport_unavailable'):
                    raise
            if time.monotonic() >= deadline:
                return {'state': 'needs_review', 'recipient_verified': False}
            time.sleep(.1)
