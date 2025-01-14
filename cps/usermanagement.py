# -*- coding: utf-8 -*-

#  This file is part of the Calibre-Web (https://github.com/janeczku/calibre-web)
#    Copyright (C) 2018-2020 OzzieIsaacs
#
#  This program is free software: you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation, either version 3 of the License, or
#  (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program. If not, see <http://www.gnu.org/licenses/>.

import base64
import binascii
from functools import wraps

import jwt
from jwt import PyJWKClient, PyJWTError
from sqlalchemy.sql.expression import func
from werkzeug.security import check_password_hash
from flask_login import login_required, login_user

from . import lm, ub, config, constants, services

jwks_client = PyJWKClient(config.config_reverse_proxy_jwt_jwks_url)


def login_required_if_no_ano(func):
    @wraps(func)
    def decorated_view(*args, **kwargs):
        if config.config_anonbrowse == 1:
            return func(*args, **kwargs)
        return login_required(func)(*args, **kwargs)

    return decorated_view


def _fetch_user_by_name(username):
    return ub.session.query(ub.User).filter(func.lower(ub.User.name) == username.lower()).first()


def _fetch_user_by_name_or_email(query):
    if '@' in query:
        return ub.session.query(ub.User).filter(
            (func.lower(ub.User.name) == query.lower()) or (func.lower(ub.User.email) == query.lower())).first()
    else:
        return _fetch_user_by_name(query)


def _get_jwt_signing_key(token):
    global jwks_client
    if jwks_client.uri != config.config_reverse_proxy_jwt_jwks_url:
        jwks_client = PyJWKClient(config.config_reverse_proxy_jwt_jwks_url)
    return jwks_client.get_signing_key_from_jwt(token)


@lm.user_loader
def load_user(user_id):
    return ub.session.query(ub.User).filter(ub.User.id == int(user_id)).first()


@lm.request_loader
def load_user_from_request(request):
    if config.config_allow_reverse_proxy_header_login:
        rp_header_name = config.config_reverse_proxy_login_header_name
        if rp_header_name:
            rp_header_username = request.headers.get(rp_header_name)
            if rp_header_username and not config.config_reverse_proxy_header_login_uses_jwt:
                user = _fetch_user_by_name(rp_header_username)\
                    if not config.config_check_reverse_proxy_header_login_email \
                    else _fetch_user_by_name_or_email(rp_header_username)
                if user:
                    login_user(user)
                    return user
            elif rp_header_username and config.config_reverse_proxy_header_login_uses_jwt:
                try:
                    signing_key = _get_jwt_signing_key(rp_header_username)
                    decoded_token = jwt.decode(rp_header_username, signing_key.key,
                                               algorithms=['RS256'],
                                               audience=config.config_reverse_proxy_jwt_audience or None,
                                               issuer=config.config_reverse_proxy_jwt_issuer or None)
                    token_username = decoded_token[config.config_reverse_proxy_jwt_id_claim]
                    user = _fetch_user_by_name(token_username) \
                        if not config.config_check_reverse_proxy_header_login_email \
                        else _fetch_user_by_name_or_email(token_username)
                    if user:
                        login_user(user)
                        return user
                except PyJWTError:
                    pass

    auth_header = request.headers.get("Authorization")
    if auth_header:
        user = load_user_from_auth_header(auth_header)
        if user:
            return user

    return


def load_user_from_auth_header(header_val):
    if header_val.startswith('Basic '):
        header_val = header_val.replace('Basic ', '', 1)
    basic_username = basic_password = ''  # nosec
    try:
        header_val = base64.b64decode(header_val).decode('utf-8')
        # Users with colon are invalid: rfc7617 page 4
        basic_username = header_val.split(':', 1)[0]
        basic_password = header_val.split(':', 1)[1]
    except (TypeError, UnicodeDecodeError, binascii.Error):
        pass
    user = _fetch_user_by_name(basic_username)
    if user and config.config_login_type == constants.LOGIN_LDAP and services.ldap:
        if services.ldap.bind_user(str(user.password), basic_password):
            return user
    if user and check_password_hash(str(user.password), basic_password):
        return user
    return
