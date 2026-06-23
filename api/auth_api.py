import jwt
import requests
from flask import (
    Blueprint,
    Response,
    current_app,
    jsonify,
    make_response,
    request,
    session,
)
from jwt import InvalidTokenError, PyJWTError
from jwt.algorithms import RSAAlgorithm

from extensions import limiter

auth_bp = Blueprint("auth", __name__)


def _set_refresh_token_cookie(response, refresh_token, max_age=None):
    response.set_cookie(
        current_app.config.get(
            "OAUTH2_REFRESH_COOKIE_NAME",
            "oauth_refresh_token",
        ),
        refresh_token,
        max_age=max_age or current_app.config.get(
            "OAUTH2_REFRESH_COOKIE_MAX_AGE",
            30 * 24 * 60 * 60,
        ),
        secure=current_app.config.get("OAUTH2_REFRESH_COOKIE_SECURE", False),
        httponly=True,
        samesite=current_app.config.get("OAUTH2_REFRESH_COOKIE_SAMESITE", "Lax"),
        path="/api/auth",
    )


def _clear_refresh_token_cookie(response):
    response.delete_cookie(
        current_app.config.get(
            "OAUTH2_REFRESH_COOKIE_NAME",
            "oauth_refresh_token",
        ),
        secure=current_app.config.get("OAUTH2_REFRESH_COOKIE_SECURE", False),
        httponly=True,
        samesite=current_app.config.get("OAUTH2_REFRESH_COOKIE_SAMESITE", "Lax"),
        path="/api/auth",
    )


def _token_response_without_refresh_token(tokens, status_code=200):
    refresh_token = tokens.pop("refresh_token", None)
    response = make_response(jsonify(tokens), status_code)
    if refresh_token:
        refresh_max_age = (
            tokens.get("refresh_expires_in")
            or tokens.get("refresh_token_expires_in")
        )
        if refresh_max_age is not None:
            refresh_max_age = int(refresh_max_age)
        _set_refresh_token_cookie(response, refresh_token, refresh_max_age)
    return response


def _validate_id_token(id_token, expected_nonce=None):
    """Validate the ID token signature and required OIDC claims."""
    jwks_url = current_app.config.get("OAUTH2_JWKS_URL")
    issuer = current_app.config.get("OAUTH2_ISSUER")
    client_id = current_app.config.get("OAUTH2_CLIENT_ID")
    timeout = current_app.config.get("OAUTH2_REQUEST_TIMEOUT", 5)

    header = jwt.get_unverified_header(id_token)
    if header.get("alg") != "RS256" or not header.get("kid"):
        raise InvalidTokenError("unsupported id_token signing header")

    jwks_response = requests.get(jwks_url, timeout=timeout)
    jwks_response.raise_for_status()
    signing_jwk = next(
        (
            key
            for key in jwks_response.json().get("keys", [])
            if key.get("kid") == header["kid"] and key.get("use", "sig") == "sig"
        ),
        None,
    )
    if not signing_jwk:
        raise InvalidTokenError("id_token signing key was not found")

    claims = jwt.decode(
        id_token,
        key=RSAAlgorithm.from_jwk(signing_jwk),
        algorithms=["RS256"],
        audience=client_id,
        issuer=issuer,
        options={"require": ["exp", "iat", "iss", "aud"]},
    )
    if expected_nonce is not None and claims.get("nonce") != expected_nonce:
        raise InvalidTokenError("id_token nonce mismatch")
    return claims


@auth_bp.route("/token", methods=["POST"])
@limiter.limit("20 per minute")
def exchange_authorization_code():
    """Exchange an authorization code for tokens through the JC4A backend."""
    data = request.get_json(silent=True) or request.form
    code = data.get("code")
    redirect_uri = data.get("redirect_uri")
    code_verifier = data.get("code_verifier")

    if not code or not redirect_uri:
        return jsonify({
            "success": False,
            "error": "code and redirect_uri are required",
        }), 400

    configured_redirect_uri = current_app.config.get("OAUTH2_REDIRECT_URI")
    if configured_redirect_uri and redirect_uri != configured_redirect_uri:
        return jsonify({
            "success": False,
            "error": "redirect_uri is not allowed",
        }), 400

    token_url = current_app.config.get("OAUTH2_TOKEN_URL")
    client_id = current_app.config.get("OAUTH2_CLIENT_ID")
    client_secret = current_app.config.get("OAUTH2_CLIENT_SECRET")
    if not token_url or not client_id or not client_secret:
        current_app.logger.error("OAuth2 token exchange configuration is incomplete")
        return jsonify({
            "success": False,
            "error": "token exchange service is not configured",
        }), 503

    token_request = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
    }
    if code_verifier:
        token_request["code_verifier"] = code_verifier

    try:
        token_response = requests.post(
            token_url,
            data=token_request,
            auth=(client_id, client_secret),
            timeout=current_app.config.get("OAUTH2_REQUEST_TIMEOUT", 5),
        )
    except requests.RequestException:
        current_app.logger.exception("OAuth2 token exchange request failed")
        return jsonify({
            "success": False,
            "error": "failed to contact token service",
        }), 502

    response = Response(
        token_response.content,
        status=token_response.status_code,
        content_type=token_response.headers.get(
            "Content-Type",
            "application/json",
        ),
    )
    if token_response.ok:
        try:
            token_data = token_response.json()
            refresh_token = token_data.get("refresh_token")
            if refresh_token:
                refresh_max_age = (
                    token_data.get("refresh_expires_in")
                    or token_data.get("refresh_token_expires_in")
                )
                if refresh_max_age is not None:
                    refresh_max_age = int(refresh_max_age)
                _set_refresh_token_cookie(
                    response,
                    refresh_token,
                    refresh_max_age,
                )
        except (TypeError, ValueError):
            current_app.logger.warning(
                "JC4A token response could not be read for refresh cookie"
            )
    return response


@auth_bp.route("/refresh", methods=["POST"])
@limiter.limit("30 per minute")
def refresh_access_token():
    """Refresh OAuth2 tokens using the HttpOnly refresh-token cookie."""
    cookie_name = current_app.config.get(
        "OAUTH2_REFRESH_COOKIE_NAME",
        "oauth_refresh_token",
    )
    refresh_token = request.cookies.get(cookie_name)
    if not refresh_token:
        return jsonify({
            "success": False,
            "error": "refresh session is missing",
        }), 401

    token_url = current_app.config.get("OAUTH2_TOKEN_URL")
    client_id = current_app.config.get("OAUTH2_CLIENT_ID")
    client_secret = current_app.config.get("OAUTH2_CLIENT_SECRET")
    if not token_url or not client_id or not client_secret:
        current_app.logger.error("OAuth2 refresh configuration is incomplete")
        return jsonify({
            "success": False,
            "error": "refresh service is not configured",
        }), 503

    try:
        token_response = requests.post(
            token_url,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
            auth=(client_id, client_secret),
            timeout=current_app.config.get("OAUTH2_REQUEST_TIMEOUT", 5),
        )
    except requests.RequestException:
        current_app.logger.exception("OAuth2 token refresh request failed")
        return jsonify({
            "success": False,
            "error": "failed to contact token service",
        }), 502

    if not token_response.ok:
        current_app.logger.warning(
            "OAuth2 token refresh failed with status %s",
            token_response.status_code,
        )
        response = make_response(jsonify({
            "success": False,
            "error": "refresh token is invalid or expired",
        }), 401)
        _clear_refresh_token_cookie(response)
        session.clear()
        return response

    try:
        tokens = token_response.json()
        id_token = tokens.get("id_token")
        if id_token:
            _validate_id_token(id_token)
    except (ValueError, KeyError, PyJWTError, requests.RequestException):
        current_app.logger.exception("JC4A returned an invalid refresh response")
        response = make_response(jsonify({
            "success": False,
            "error": "invalid token response",
        }), 502)
        _clear_refresh_token_cookie(response)
        session.clear()
        return response

    return _token_response_without_refresh_token(
        tokens,
        token_response.status_code,
    )


def _get_logout_tokens():
    """Read OAuth2 tokens from the Authorization header and refresh cookie."""
    authorization = request.headers.get("Authorization", "")
    scheme, _, credentials = authorization.partition(" ")
    data = request.get_json(silent=True) or request.form
    access_token = data.get("access_token") or data.get("token")
    if scheme.lower() == "bearer" and credentials.strip():
        access_token = credentials.strip()

    cookie_name = current_app.config.get(
        "OAUTH2_REFRESH_COOKIE_NAME",
        "oauth_refresh_token",
    )
    refresh_token = request.cookies.get(cookie_name) or data.get("refresh_token")
    return access_token, refresh_token


def _revoke_token(revocation_url, token, token_type_hint, client_auth, timeout):
    return requests.post(
        revocation_url,
        data={
            "token": token,
            "token_type_hint": token_type_hint,
        },
        auth=client_auth,
        timeout=timeout,
    )


@auth_bp.route("/logout", methods=["POST"])
@limiter.limit("20 per minute")
def logout():
    """Revoke OAuth2 tokens and clear the local business session."""
    access_token, refresh_token = _get_logout_tokens()
    if not access_token and not refresh_token:
        return jsonify({
            "success": False,
            "error": "access_token or refresh_token is required",
        }), 400

    revocation_url = current_app.config.get("OAUTH2_REVOCATION_URL")
    client_id = current_app.config.get("OAUTH2_CLIENT_ID")
    client_secret = current_app.config.get("OAUTH2_CLIENT_SECRET")
    if not revocation_url or not client_id or not client_secret:
        current_app.logger.error("OAuth2 revocation configuration is incomplete")
        return jsonify({
            "success": False,
            "error": "logout service is not configured",
        }), 503

    try:
        timeout = current_app.config.get("OAUTH2_REQUEST_TIMEOUT", 5)
        client_auth = (client_id, client_secret)
        tokens = (
            (refresh_token, "refresh_token"),
            (access_token, "access_token"),
        )
        responses = [
            _revoke_token(
                revocation_url,
                token,
                token_type_hint,
                client_auth,
                timeout,
            )
            for token, token_type_hint in tokens
            if token
        ]
    except requests.RequestException:
        current_app.logger.exception("OAuth2 token revocation request failed")
        return jsonify({
            "success": False,
            "error": "failed to contact logout service",
        }), 502

    failed_responses = [response for response in responses if not response.ok]
    if failed_responses:
        current_app.logger.warning(
            "OAuth2 token revocation failed with status codes %s",
            [response.status_code for response in failed_responses],
        )
        session.clear()
        response = make_response(jsonify({
            "success": False,
            "error": "token revocation failed",
        }), 502)
        _clear_refresh_token_cookie(response)
        return response

    session.clear()
    response = make_response(jsonify({
        "success": True,
        "message": "logout successful",
    }), 200)
    _clear_refresh_token_cookie(response)
    return response
