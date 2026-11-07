from __future__ import annotations

import logging
import os
import time
from functools import wraps

from flask import Flask, jsonify, make_response, request
from flask_cors import CORS
from werkzeug.middleware.proxy_fix import ProxyFix

try:
    from .binance import PUBLIC_DATA_ERROR, fetch_btc_put_snapshot
    from .risk_accounts import load_account_registry
    from .risk_auth import (
        COOKIE_MAX_AGE_SECONDS,
        COOKIE_PATH,
        RiskAuthError,
        RiskAuthenticationRequired,
        RiskAuthManager,
        RiskAuthOriginRejected,
    )
    from .risk_binance import (
        BinancePrivateClient,
        PrivateAccountCache,
        RiskPublicCache,
    )
    from .risk_service import (
        LiquidationRiskService,
        RiskRequestError,
        RiskServiceError,
        environment_cache_settings,
        error_response,
    )
except ImportError:
    # Gunicorn's legacy app:app entry point imports from the server directory.
    from binance import PUBLIC_DATA_ERROR, fetch_btc_put_snapshot
    from risk_accounts import load_account_registry
    from risk_auth import (
        COOKIE_MAX_AGE_SECONDS,
        COOKIE_PATH,
        RiskAuthError,
        RiskAuthenticationRequired,
        RiskAuthManager,
        RiskAuthOriginRejected,
    )
    from risk_binance import BinancePrivateClient, PrivateAccountCache, RiskPublicCache
    from risk_service import (
        LiquidationRiskService,
        RiskRequestError,
        RiskServiceError,
        environment_cache_settings,
        error_response,
    )


logger = logging.getLogger(__name__)
RISK_API_PREFIX = "/api/options/liquidation-risk"


def _request_timeout_seconds() -> float:
    try:
        timeout = float(os.environ.get("BINANCE_REQUEST_TIMEOUT", "10"))
    except ValueError:
        return 10.0
    return timeout if timeout > 0 else 10.0


def build_risk_service() -> LiquidationRiskService:
    ttl_seconds, stale_max_seconds = environment_cache_settings()
    client = BinancePrivateClient(timeout_seconds=_request_timeout_seconds())
    return LiquidationRiskService(
        load_account_registry(),
        RiskPublicCache(),
        PrivateAccountCache(
            client,
            ttl_seconds=ttl_seconds,
            stale_max_seconds=stale_max_seconds,
        ),
    )


def create_app(
    risk_service: LiquidationRiskService | None = None,
    risk_auth: RiskAuthManager | None = None,
) -> Flask:
    app = Flask(__name__)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1)
    CORS(
        app,
        resources={r"/api/options/btc-put": {"origins": "*"}},
    )
    service = risk_service or build_risk_service()
    auth = risk_auth or RiskAuthManager.from_environment()

    def auth_error_response(error: RiskAuthError):
        response = jsonify({"error": {"code": error.code}})
        response.status_code = error.status_code
        if error.retry_after_seconds is not None:
            response.headers["Retry-After"] = str(error.retry_after_seconds)
        return response

    def client_ip() -> str:
        remote = request.remote_addr or "unknown"
        if remote in {"127.0.0.1", "::1"}:
            return request.headers.get("X-Real-IP", remote)
        return remote

    def require_same_origin() -> None:
        origin = request.headers.get("Origin", "").rstrip("/")
        expected = request.host_url.rstrip("/")
        if not origin or origin != expected:
            raise RiskAuthOriginRejected("Risk authentication requires same-origin")

    def current_token() -> str | None:
        try:
            return request.cookies.get(auth.cookie_name)
        except RiskAuthError:
            return None

    def set_session_cookie(response, token: str):
        response.set_cookie(
            auth.cookie_name,
            token,
            max_age=COOKIE_MAX_AGE_SECONDS,
            secure=auth.cookie_secure,
            httponly=True,
            samesite="Strict",
            path=COOKIE_PATH,
        )

    def clear_session_cookie(response):
        names = {"__Secure-options_calc_risk_session", "options_calc_risk_session_dev"}
        for name in names:
            response.delete_cookie(
                name,
                secure=name.startswith("__Secure-"),
                httponly=True,
                samesite="Strict",
                path=COOKIE_PATH,
            )

    def require_risk_auth(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            token = current_token()
            try:
                auth.validate_session(
                    token,
                    client_ip=client_ip(),
                    user_agent=request.headers.get("User-Agent"),
                )
            except RiskAuthError as error:
                return auth_error_response(error)
            response = make_response(view(*args, **kwargs))
            set_session_cookie(response, token or "")
            return response

        return wrapped

    @app.after_request
    def private_cache_control(response):
        if request.path.startswith(RISK_API_PREFIX):
            response.headers.set("Cache-Control", "no-store, private")
            response.headers.add("Vary", "Cookie")
            response.headers.pop("WWW-Authenticate", None)
        return response

    @app.get("/api/health")
    def health():
        return jsonify({"ok": True, "now": int(time.time() * 1000)})

    @app.get("/api/options/btc-put")
    def btc_put_options():
        try:
            return jsonify(fetch_btc_put_snapshot())
        except RuntimeError:
            logger.error("BTC put options request failed (no cached snapshot)")
            return (
                jsonify({"error": PUBLIC_DATA_ERROR}),
                502,
            )

    @app.get(f"{RISK_API_PREFIX}/auth/session")
    def risk_auth_session():
        token = current_token()
        try:
            auth.validate_session(
                token,
                client_ip=client_ip(),
                user_agent=request.headers.get("User-Agent"),
            )
        except RiskAuthenticationRequired:
            response = jsonify({"authenticated": False})
            clear_session_cookie(response)
            return response
        except RiskAuthError as error:
            return auth_error_response(error)
        response = jsonify({"authenticated": True})
        set_session_cookie(response, token or "")
        return response

    @app.post(f"{RISK_API_PREFIX}/auth/login")
    def risk_auth_login():
        try:
            require_same_origin()
            payload = request.get_json(silent=True) if request.is_json else None
            if not isinstance(payload, dict) or not isinstance(
                payload.get("password"), str
            ):
                return jsonify({"error": {"code": "invalid_request"}}), 400
            session = auth.authenticate(
                payload["password"],
                client_ip=client_ip(),
                user_agent=request.headers.get("User-Agent"),
            )
        except RiskAuthError as error:
            return auth_error_response(error)
        response = jsonify({"authenticated": True})
        set_session_cookie(response, session.token)
        return response

    @app.post(f"{RISK_API_PREFIX}/auth/logout")
    def risk_auth_logout():
        try:
            require_same_origin()
            auth.logout(current_token())
        except RiskAuthError as error:
            if not isinstance(error, RiskAuthenticationRequired):
                return auth_error_response(error)
        response = make_response("", 204)
        clear_session_cookie(response)
        return response

    @app.get(f"{RISK_API_PREFIX}/auth/authorize")
    def risk_auth_authorize():
        try:
            auth.validate_session(
                current_token(),
                client_ip=client_ip(),
                user_agent=request.headers.get("User-Agent"),
            )
        except RiskAuthError as error:
            return auth_error_response(error)
        return "", 204

    @app.get(f"{RISK_API_PREFIX}/accounts")
    @require_risk_auth
    def liquidation_risk_accounts():
        try:
            return jsonify(service.accounts_response())
        except RiskServiceError as error:
            return jsonify(error_response("service_unavailable", error.issues)), 503

    @app.post(f"{RISK_API_PREFIX}/evaluate")
    @require_risk_auth
    def liquidation_risk_evaluate():
        try:
            require_same_origin()
        except RiskAuthError as error:
            return auth_error_response(error)
        payload = request.get_json(silent=True) if request.is_json else None
        try:
            return jsonify(service.evaluate(payload))
        except RiskRequestError as error:
            return jsonify(error_response("invalid_request", error.issues)), 400
        except RiskServiceError as error:
            return jsonify(error_response("service_unavailable", error.issues)), 503

    return app


app = create_app()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5004"))
    host = os.environ.get("HOST", "127.0.0.1")
    app.run(host=host, port=port)
