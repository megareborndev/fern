# license.py
import os
import threading
import time
import logging
import secrets
from datetime import datetime, timedelta, timezone
import json

from utils import db
from flask import Flask, request
from flask_socketio import SocketIO, emit


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("license-server")

db_conn = None


def init_db():
    global db_conn

    db_conn = db.connect()
    db.setup_tables(db_conn)

    print("SUPABASE CONNECTED")


app = Flask(__name__)
app.config['SECRET_KEY'] = 'license-secret-key'

socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    async_mode='threading',
)


CHANGELOG_FILE = os.path.join(
    os.path.dirname(__file__),
    'json/changelog.json',
)

changelog = []


def fmt_iso(dt):
    return dt.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')


def parse_iso(value):
    if not value:
        return None

    text = str(value).strip()

    if text.endswith('Z'):
        text = text[:-1]

    try:
        dt = datetime.fromisoformat(text)

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        return dt.astimezone(timezone.utc)

    except (TypeError, ValueError):
        return None


_LOCK = threading.RLock()
_CHANGELOG_LOCK = threading.RLock()
_POLLER_STARTED = False


def load_changelog():
    global changelog

    try:
        with open(CHANGELOG_FILE, 'r', encoding='utf-8') as file:
            data = json.load(file)

        changelog = data if isinstance(data, list) else []

        logger.info(
            "Loaded %d changelog entries.",
            len(changelog),
        )

    except (FileNotFoundError, json.JSONDecodeError):
        changelog = []


def save_changelog():
    with _CHANGELOG_LOCK:
        with open(CHANGELOG_FILE, 'w', encoding='utf-8') as file:
            json.dump(changelog, file, indent=2)

        logger.info("Changelog saved.")


def get_trial_record(user_id: str):
    user_id = str(user_id).strip()

    if not user_id:
        return None, None

    return db.find_trial_user(db_conn, user_id)


def is_trial_user(user_id: str) -> bool:
    trial_key, trial = get_trial_record(user_id)

    return (
        trial_key is not None
        and isinstance(trial, dict)
    )


def extend_user_license(user_id: str, days: int) -> bool:
    with _LOCK:
        license_key, license_info = db.find_bound_to(
            db_conn,
            str(user_id),
        )

        if (
            license_key is None
            or not isinstance(license_info, dict)
        ):
            return False

        expires_str = license_info.get('expires')

        if expires_str:
            expires_dt = parse_iso(expires_str)

            if expires_dt is None:
                expires_dt = datetime.now(timezone.utc)
        else:
            expires_dt = datetime.now(timezone.utc)

        license_info['expires'] = fmt_iso(
            expires_dt + timedelta(days=days)
        )

        db.add_document(
            db_conn,
            "licences",
            license_key,
            license_info,
        )

        return True


def complete_referral(invitee_id: str) -> bool:
    invitee_id = str(invitee_id).strip()

    if not invitee_id:
        return False

    referral_key, referral = db.find_invitee_id(
        db_conn,
        invitee_id,
    )

    if (
        referral_key is None
        or not isinstance(referral, dict)
    ):
        return False

    if referral.get('status') != 'pending':
        return False

    inviter_id = referral.get('inviter_id')

    if not inviter_id:
        logger.warning(
            "Referral for invitee %s has no inviter_id.",
            invitee_id,
        )
        return False

    if not extend_user_license(inviter_id, 3):
        logger.warning(
            "Failed to extend license for %s.",
            inviter_id,
        )
        return False

    referral['status'] = 'completed'
    referral['completed_at'] = fmt_iso(
        datetime.now(timezone.utc)
    )

    db.add_document(
        db_conn,
        "referrals",
        referral_key,
        referral,
    )

    logger.info(
        "Referral completed: %s invited %s.",
        inviter_id,
        invitee_id,
    )

    return True


def referral_poller():
    while True:
        time.sleep(60)

        try:
            referral_documents = db.list_all_documents(
                db_conn,
                "referrals",
            )

            for document in referral_documents:
                referral = document.get('value')

                if not isinstance(referral, dict):
                    continue

                if referral.get('status') != 'pending':
                    continue

                invitee_id = referral.get('invitee_id')

                if not invitee_id:
                    continue

                try:
                    license_key, _ = db.find_bound_to(
                        db_conn,
                        str(invitee_id),
                    )

                    if license_key is not None:
                        complete_referral(str(invitee_id))

                except Exception:
                    logger.exception(
                        "Failed to process referral for invitee %s.",
                        invitee_id,
                    )

        except Exception:
            logger.exception("Referral poller error.")


def validate_license_key(license_key, user_id=None):
    license_key = str(license_key or '').strip()

    if not license_key:
        return False, "No key provided.", None

    license_info = db.get_document(
        db_conn,
        "licences",
        license_key,
    )

    if not license_info:
        return False, "License key not recognized.", None

    if not isinstance(license_info, dict):
        return False, "License key formatting error.", None

    if not license_info.get('active', True):
        return False, "License is inactive.", None

    expires_str = license_info.get('expires')

    if expires_str:
        expires_dt = parse_iso(expires_str)

        if (
            expires_dt is not None
            and datetime.now(timezone.utc) > expires_dt
        ):
            return False, "License has expired.", None

    bound_to = license_info.get('bound_to')

    if bound_to is not None:
        if user_id is None:
            return True, "License valid.", license_info.get('expires')

        if str(bound_to) != str(user_id):
            return (
                False,
                f"License is bound to another user (ID: {bound_to}).",
                None,
            )

        return True, "License valid.", license_info.get('expires')

    if user_id is None:
        return (
            True,
            "License valid (unbound).",
            license_info.get('expires'),
        )

    license_info['bound_to'] = str(user_id)

    db.add_document(
        db_conn,
        "licences",
        license_key,
        license_info,
    )

    logger.info(
        "License %s bound to user %s.",
        license_key,
        user_id,
    )

    threading.Thread(
        target=complete_referral,
        args=(str(user_id),),
        daemon=True,
    ).start()

    return (
        True,
        "License bound and valid.",
        license_info.get('expires'),
    )


def claim_trial(
    user_id: str,
    ip: str,
    user_agent: str,
) -> tuple[bool, str | None, str | None]:
    user_id = str(user_id).strip()

    if not user_id:
        return False, None, "user_id required"

    now = datetime.now(timezone.utc)

    existing_trial_key, existing_trial = db.find_trial_user(
        db_conn,
        user_id,
    )

    if existing_trial_key is not None:
        if not isinstance(existing_trial, dict):
            return False, None, "Invalid trial record."

        existing_license_key = existing_trial.get('license_key')

        if existing_license_key:
            existing_license = db.get_document(
                db_conn,
                "licences",
                existing_license_key,
            )

            if isinstance(existing_license, dict):
                expires_dt = parse_iso(
                    existing_license.get('expires')
                )

                if expires_dt and expires_dt > now:
                    return True, existing_license_key, None

        trial_key = existing_license_key or (
            "TRIAL-" + secrets.token_urlsafe(16)
        )

        while db.get_document(
            db_conn,
            "licences",
            trial_key,
        ):
            if trial_key == existing_license_key:
                break

            trial_key = "TRIAL-" + secrets.token_urlsafe(16)

    else:
        trial_key = "TRIAL-" + secrets.token_urlsafe(16)

        while db.get_document(
            db_conn,
            "licences",
            trial_key,
        ):
            trial_key = "TRIAL-" + secrets.token_urlsafe(16)

    expires = fmt_iso(
        now + timedelta(hours=24)
    )

    db.add_document(
        db_conn,
        "licences",
        trial_key,
        {
            "bound_to": user_id,
            "expires": expires,
            "active": True,
            "trial": True,
        },
    )

    db.add_document(
        db_conn,
        "trials",
        user_id,
        {
            "user_id": user_id,
            "ip": ip,
            "user_agent": user_agent,
            "claimed_at": fmt_iso(now),
            "license_key": trial_key,
        },
    )

    logger.info(
        "Trial claimed for user %s, key %s.",
        user_id,
        trial_key,
    )

    return True, trial_key, None


@socketio.on('connect')
def handle_connect():
    logger.info(
        "Client connected: %s",
        request.sid,
    )


@socketio.on('disconnect')
def handle_disconnect():
    logger.info(
        "Client disconnected: %s",
        request.sid,
    )


@socketio.on('validate_license')
def handle_validate_license(data):
    data = data or {}

    license_key = str(
        data.get('license_key', '')
    ).strip()

    user_id = data.get('user_id')

    valid, message, expires = validate_license_key(
        license_key,
        user_id,
    )

    license_info = db.get_document(
        db_conn,
        "licences",
        license_key,
    )

    if not isinstance(license_info, dict):
        valid = False
        license_info = {}

    trial = bool(
        license_info.get('trial')
        or license_key.startswith('TRIAL-')
    )

    if user_id and is_trial_user(str(user_id)):
        trial = True

    emit('license_response', {
        'valid': valid,
        'message': message,
        'expires': expires,
        'trial': trial,
        'bound_to': (
            license_info.get('bound_to')
            if valid
            else None
        ),
    })


@socketio.on('reload_licenses')
def handle_reload_licenses(data=None):
    emit(
        'reload_response',
        {
            'status': 'ok',
            'count': db.count_documents(
                db_conn,
                "licences",
            ),
        },
    )


@app.route('/api/admin/reload', methods=['POST'])
def api_admin_reload():
    if not _admin_key_ok():
        return {'error': 'Unauthorized'}, 401

    return {
        'status': 'ok',
        'licenses': db.count_documents(
            db_conn,
            "licences",
        ),
        'trials': db.count_documents(
            db_conn,
            "trials",
        ),
        'referrals': db.count_documents(
            db_conn,
            "referrals",
        ),
    }


@app.route('/api/reset-license', methods=['POST'])
def api_reset_license():
    if not _admin_key_ok():
        return {'error': 'Unauthorized'}, 401

    data = request.get_json(silent=True) or {}

    license_key = str(
        data.get('license_key', '')
    ).strip()

    if not license_key:
        return {
            'error': 'license_key required',
        }, 400

    license_info = db.get_document(
        db_conn,
        "licences",
        license_key,
    )

    if not isinstance(license_info, dict):
        return {
            'error': 'License not found',
        }, 404

    if 'bound_to' not in license_info:
        return {
            'status': 'ok',
            'message': 'License was already unbound.',
        }

    del license_info['bound_to']

    db.add_document(
        db_conn,
        "licences",
        license_key,
        license_info,
    )

    logger.info(
        "License %s reset.",
        license_key,
    )

    return {
        'status': 'ok',
        'message': 'License reset successfully.',
    }


@app.route('/api/validate-license', methods=['POST'])
def api_validate_license():
    data = request.get_json(silent=True) or {}

    license_key = str(
        data.get('license_key', '')
    ).strip()

    user_id = data.get('user_id')

    valid, message, expires = validate_license_key(
        license_key,
        user_id,
    )

    license_info = db.get_document(
        db_conn,
        "licences",
        license_key,
    )

    if not isinstance(license_info, dict):
        license_info = {}

    trial = bool(
        license_info.get('trial')
        or license_key.startswith('TRIAL-')
    )

    if user_id and is_trial_user(str(user_id)):
        trial = True

    return {
        'valid': valid,
        'message': message,
        'expires': expires,
        'trial': trial,
        'bound_to': (
            license_info.get('bound_to')
            if valid
            else None
        ),
    }


@app.route('/api/extend-license', methods=['POST'])
def api_extend_license():
    data = request.get_json(silent=True) or {}

    user_id = data.get('user_id')
    days = data.get('days', 0)

    if (
        not user_id
        or not isinstance(days, int)
        or days <= 0
    ):
        return {
            'error': 'Invalid parameters',
        }, 400

    if extend_user_license(str(user_id), days):
        return {
            'status': 'ok',
            'message': f'Added {days} days to user {user_id}',
        }, 200

    return {
        'error': 'No license found for that user',
    }, 404


@app.route('/api/referral/invite', methods=['POST'])
def api_referral_invite():
    data = request.get_json(silent=True) or {}

    inviter_id = str(
        data.get('inviter_id', '')
    ).strip()

    invitee_id = str(
        data.get('invitee_id', '')
    ).strip()

    license_key = str(
        data.get('license_key', '')
    ).strip()

    if not inviter_id or not invitee_id:
        return {
            'error': 'inviter_id and invitee_id required',
        }, 400

    if inviter_id == invitee_id:
        return {
            'error': 'Cannot invite yourself',
        }, 400

    if is_trial_user(inviter_id):
        return {
            'error': 'trial_user',
            'message': 'Trial accounts cannot send referrals.',
        }, 403

    if license_key:
        if license_key.startswith('TRIAL-'):
            return {
                'error': 'trial_user',
                'message': 'Trial accounts cannot send referrals.',
            }, 403

        license_info = db.get_document(
            db_conn,
            "licences",
            license_key,
        )

        if not isinstance(license_info, dict):
            return {
                'error': 'not_found',
                'message': 'License not found.',
            }, 404

        if (
            license_info.get('trial')
            or license_info.get('trials')
            or license_info.get('type') == 'trial'
        ):
            return {
                'error': 'trial_user',
                'message': 'Trial accounts cannot send referrals.',
            }, 403

    referral_key = None

    for document in db.list_all_documents(
        db_conn,
        "referrals",
    ):
        referral = document.get('value')

        if not isinstance(referral, dict):
            continue

        if (
            referral.get('inviter_id') == inviter_id
            and referral.get('invitee_id') == invitee_id
            and referral.get('status') == 'pending'
        ):
            return {
                'error': 'Already invited',
            }, 409

    referral_key = secrets.token_urlsafe(16)

    while db.get_document(
        db_conn,
        "referrals",
        referral_key,
    ):
        referral_key = secrets.token_urlsafe(16)

    referral = {
        'inviter_id': inviter_id,
        'invitee_id': invitee_id,
        'status': 'pending',
        'created_at': fmt_iso(
            datetime.now(timezone.utc)
        ),
        'completed_at': None,
    }

    db.add_document(
        db_conn,
        "referrals",
        referral_key,
        referral,
    )

    logger.info(
        "Referral created: %s -> %s",
        inviter_id,
        invitee_id,
    )

    return {
        'message': 'Referral created',
        'inviter_id': inviter_id,
        'invitee_id': invitee_id,
    }, 201


@app.route('/api/referral/stats', methods=['GET'])
def api_referral_stats():
    user_id = request.args.get('user_id')

    if not user_id:
        return {
            'error': 'user_id required',
        }, 400

    user_id = str(user_id).strip()

    if not user_id:
        return {
            'error': 'user_id required',
        }, 400

    total = 0
    completed = 0

    for document in db.list_all_documents(
        db_conn,
        "referrals",
    ):
        referral = document.get('value')

        if not isinstance(referral, dict):
            continue

        if str(
            referral.get('inviter_id', '')
        ).strip() != user_id:
            continue

        total += 1

        if referral.get('status') == 'completed':
            completed += 1

    return {
        'user_id': user_id,
        'total_invites': total,
        'completed': completed,
        'pending': total - completed,
    }


@app.route('/api/trial/claim', methods=['POST'])
def api_trial_claim():
    data = request.get_json(silent=True) or {}

    user_id = data.get('user_id')

    if not user_id:
        return {
            'error': 'user_id required',
        }, 400

    ip = request.remote_addr or 'unknown'
    user_agent = request.headers.get(
        'User-Agent',
        'unknown',
    )

    success, license_key, error = claim_trial(
        user_id,
        ip,
        user_agent,
    )

    if not success:
        return {
            'error': error,
        }, 409

    license_info = db.get_document(
        db_conn,
        "licences",
        license_key,
    )

    if not isinstance(license_info, dict):
        return {
            'error': 'License not found',
        }, 500

    return {
        'license_key': license_key,
        'expires': license_info.get('expires'),
    }


@app.route('/api/user-status', methods=['GET'])
def api_user_status():
    user_id = request.args.get('user_id')

    if not user_id:
        return {
            'error': 'user_id required',
        }, 400

    user_id = str(user_id).strip()

    license_key, license_info = db.find_bound_to(
        db_conn,
        user_id,
    )

    if (
        license_key is None
        or not isinstance(license_info, dict)
    ):
        return {
            'user_id': user_id,
            'is_fern_user': False,
        }

    if not license_info.get('active', True):
        return {
            'user_id': user_id,
            'is_fern_user': False,
        }

    expires_str = license_info.get('expires')

    if not expires_str:
        expires_dt = datetime.max.replace(
            tzinfo=timezone.utc
        )
    else:
        expires_dt = parse_iso(expires_str)

    return {
        'user_id': user_id,
        'is_fern_user': True,
        'license_key': license_key,
        'expires': license_info.get('expires'),
        'trial': bool(license_info.get('trial')),
        'valid': bool(
            expires_dt is None
            or expires_dt > datetime.now(timezone.utc)
        ),
    }


@app.route('/api/trial/status', methods=['GET'])
def api_trial_status():
    user_id = request.args.get('user_id')

    if not user_id:
        return {
            'error': 'user_id required',
        }, 400

    used = is_trial_user(str(user_id))

    return {
        'available': not used,
        'used': used,
    }


def _admin_key_ok():
    auth = request.headers.get('X-Admin-Key')

    expected = os.environ.get(
        'ADMIN_KEY',
        'u6a5r5lDNBFKPq0xX-mYHF6rDOTq7q5E',
    )

    return bool(auth and auth == expected)


@app.route('/api/admin/data', methods=['GET'])
def api_admin_data():
    if not _admin_key_ok():
        return {
            'error': 'Unauthorized',
        }, 401

    license_documents = db.list_all_documents(
        db_conn,
        "licences",
    )

    license_list = [
        {
            'key': item['key'],
            'created': item['value'].get('created'),
            'expires': item['value'].get('expires'),
            'active': item['value'].get('active', True),
            'trials': bool(item['value'].get('trial')),
            'bound_to': item['value'].get('bound_to'),
        }
        for item in license_documents
        if isinstance(item.get('value'), dict)
    ]

    license_values = [
        item['value']
        for item in license_documents
        if isinstance(item.get('value'), dict)
    ]

    referral_documents = db.list_all_documents(
        db_conn,
        "referrals",
    )

    referral_list = [
        {
            'key': item['key'],
            **item['value'],
        }
        for item in referral_documents
        if isinstance(item.get('value'), dict)
    ]

    trial_documents = db.list_all_documents(
        db_conn,
        "trials",
    )

    trial_list = [
        {
            'key': item['key'],
            **item['value'],
        }
        for item in trial_documents
        if isinstance(item.get('value'), dict)
    ]

    stats = {
        'total': len(license_values),
        'active': sum(
            1
            for info in license_values
            if info.get('active', True)
        ),
        'bound': sum(
            1
            for info in license_values
            if info.get('bound_to')
        ),
        'trials': sum(
            1
            for info in license_values
            if info.get('trial')
        ),
        'referrals': len(referral_list),
        'trials_records': len(trial_list),
    }

    return {
        'stats': stats,
        'licenses': license_list,
        'referrals': referral_list,
        'trials': trial_list,
    }


@app.route('/api/admin/license', methods=['POST'])
def api_admin_create_license():
    if not _admin_key_ok():
        return {
            'error': 'Unauthorized',
        }, 401

    data = request.get_json(silent=True) or {}
    days = data.get('days')

    try:
        days = int(days) if days else 0
    except (TypeError, ValueError):
        return {
            'error': 'days must be an integer',
        }, 400

    if days < 0:
        days = 0

    license_key = secrets.token_urlsafe(32)

    while db.get_document(
        db_conn,
        "licences",
        license_key,
    ):
        license_key = secrets.token_urlsafe(32)

    now = datetime.now(timezone.utc)

    license_info = {
        'created': fmt_iso(now),
        'active': True,
        'bound_to': None,
    }

    if days:
        license_info['expires'] = fmt_iso(
            now + timedelta(days=days)
        )

    db.add_document(
        db_conn,
        "licences",
        license_key,
        license_info,
    )

    logger.info(
        "License created by admin: %s (%sd).",
        license_key,
        days,
    )

    return {
        'license_key': license_key,
        **license_info,
    }


@app.route('/api/changelog', methods=['GET'])
def api_changelog():
    with _CHANGELOG_LOCK:
        return {
            'entries': changelog,
        }


@app.route('/api/admin/changelog', methods=['POST'])
def api_admin_changelog():
    if not _admin_key_ok():
        return {
            'error': 'Unauthorized',
        }, 401

    data = request.get_json(silent=True) or {}

    title = str(
        data.get('title') or ''
    ).strip()

    body = str(
        data.get('body') or ''
    ).strip()

    version = str(
        data.get('version') or 'latest'
    ).strip()

    if not title or not body:
        return {
            'error': 'title and body required',
        }, 400

    entry = {
        'id': secrets.token_urlsafe(12),
        'version': version,
        'title': title,
        'body': body,
        'created_at': fmt_iso(
            datetime.now(timezone.utc)
        ),
    }

    with _CHANGELOG_LOCK:
        changelog.append(entry)
        save_changelog()

    logger.info(
        "Changelog entry added by admin: %s",
        title,
    )

    return entry, 201


@app.route('/api/admin/changelog/<entry_id>', methods=['DELETE'])
def api_admin_changelog_delete(entry_id):
    if not _admin_key_ok():
        return {
            'error': 'Unauthorized',
        }, 401

    with _CHANGELOG_LOCK:
        for index, entry in enumerate(changelog):
            if entry.get('id') == entry_id:
                changelog.pop(index)
                save_changelog()

                return {
                    'status': 'ok',
                }

    return {
        'error': 'not found',
    }, 404


@app.route('/health')
def health():
    return 'OK'


if __name__ == '__main__':
    init_db()
    load_changelog()

    if not _POLLER_STARTED:
        thread = threading.Thread(
            target=referral_poller,
            daemon=True,
        )
        thread.start()

        _POLLER_STARTED = True

        logger.info("Referral poller started.")

    socketio.run(
        app,
        host='127.0.0.1',
        port=5002,
        debug=False,
        allow_unsafe_werkzeug=True,
    )