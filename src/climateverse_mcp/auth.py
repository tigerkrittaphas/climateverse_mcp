"""OAuth for the hosted HTTP server, backed by an AWS Cognito user pool."""

from fastmcp.server.auth import AuthProvider

from climateverse_mcp.settings import Settings

_REQUIRED = (
    ("CLIMATEVERSE_PUBLIC_BASE_URL", "public_base_url"),
    ("CLIMATEVERSE_COGNITO_USER_POOL_ID", "cognito_user_pool_id"),
    ("CLIMATEVERSE_COGNITO_CLIENT_ID", "cognito_client_id"),
    ("CLIMATEVERSE_COGNITO_CLIENT_SECRET", "cognito_client_secret"),
    ("CLIMATEVERSE_OAUTH_JWT_SIGNING_KEY", "oauth_jwt_signing_key"),
    ("CLIMATEVERSE_OAUTH_STORAGE_KEY", "oauth_storage_key"),
    ("CLIMATEVERSE_OAUTH_TABLE", "oauth_table"),
)


class AuthConfigError(RuntimeError):
    """The HTTP server cannot start with the configured auth settings."""


def build_auth(settings: Settings) -> AuthProvider | None:
    """Return the Cognito provider, or None when explicitly running open.

    Partial configuration is an error rather than a silent fallback to an
    unauthenticated server.
    """
    missing = [env for env, field in _REQUIRED if not getattr(settings, field)]
    if len(missing) == len(_REQUIRED) and settings.allow_unauthenticated:
        return None
    if missing:
        raise AuthConfigError(
            "HTTP transport requires OAuth; missing " + ", ".join(missing)
            + ". Set CLIMATEVERSE_ALLOW_UNAUTHENTICATED=true only for local testing."
        )

    # Imported lazily: the AWS dependencies ship in the optional `aws` extra.
    from cryptography.fernet import Fernet
    from fastmcp.server.auth.providers.aws import AWSCognitoProvider
    from key_value.aio.stores.dynamodb import DynamoDBStore
    from key_value.aio.wrappers.encryption import FernetEncryptionWrapper

    storage = FernetEncryptionWrapper(
        DynamoDBStore(
            table_name=settings.oauth_table,
            region_name=settings.cognito_region,
            # Terraform owns the table; the task role cannot create tables.
            auto_create=False,
        ),
        fernet=Fernet(settings.oauth_storage_key),
    )
    return AWSCognitoProvider(
        user_pool_id=settings.cognito_user_pool_id,
        aws_region=settings.cognito_region,
        client_id=settings.cognito_client_id,
        client_secret=settings.cognito_client_secret,
        base_url=settings.public_base_url,
        client_storage=storage,
        jwt_signing_key=settings.oauth_jwt_signing_key,
        allowed_client_redirect_uris=settings.oauth_allowed_redirect_uris,
    )
