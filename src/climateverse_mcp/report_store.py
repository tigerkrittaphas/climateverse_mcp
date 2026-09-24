"""S3 storage for reports rendered by the hosted server.

A hosted server cannot write to the user's disk, so reports go to a private
bucket and are served back from /reports/<id>.html. The random id is the only
access control — anyone holding the link can read the report — which matches
how a report is shared once written. Presigned S3 URLs are avoided because
they die with the task role's temporary credentials, within hours.
"""

import re
import uuid
from functools import lru_cache

_PREFIX = "reports/"
_REPORT_ID = re.compile(r"^[0-9a-f]{32}$")

# Reports are agent-authored HTML served from the same origin as the OAuth
# endpoints. `sandbox` gives the page an opaque origin, so nothing in it can
# read this origin's cookies or call its endpoints even if markup slips past
# render_report's checks; the rest allows only the inline, self-contained
# styling and data: images a report is built from.
REPORT_CSP = (
    "sandbox; default-src 'none'; style-src 'unsafe-inline'; "
    "img-src data:; font-src data:"
)


@lru_cache
def _s3():
    import boto3  # optional `aws` extra

    return boto3.client("s3")


def save_report(bucket: str, document: str) -> str:
    """Store `document` and return its report id."""
    report_id = uuid.uuid4().hex
    _s3().put_object(
        Bucket=bucket,
        Key=f"{_PREFIX}{report_id}.html",
        Body=document.encode("utf-8"),
        ContentType="text/html; charset=utf-8",
    )
    return report_id


def load_report(bucket: str, report_id: str) -> bytes | None:
    """Return the stored report, or None for a malformed or unknown id."""
    if not _REPORT_ID.fullmatch(report_id):
        return None
    s3 = _s3()
    try:
        response = s3.get_object(Bucket=bucket, Key=f"{_PREFIX}{report_id}.html")
    except s3.exceptions.NoSuchKey:
        return None
    return response["Body"].read()
