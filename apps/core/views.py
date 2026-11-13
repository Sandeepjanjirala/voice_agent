import logging

from django.db import DatabaseError, connection
from rest_framework import status
from rest_framework.decorators import (
    api_view,
    authentication_classes,
    permission_classes,
)
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response

logger = logging.getLogger(__name__)


def _database_ok() -> bool:
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except DatabaseError:
        logger.exception("health check: database unavailable")
        return False
    return True


@api_view(["GET"])
@authentication_classes([])
@permission_classes([AllowAny])
def health(request: Request) -> Response:
    """Report whether Django is up and can reach its database.

    Covers the Django process only; the voice worker is a separate process
    with its own lifecycle.
    """
    database_ok = _database_ok()
    return Response(
        {
            "status": "ok" if database_ok else "degraded",
            "service": "django",
            "database": "ok" if database_ok else "unavailable",
        },
        status=status.HTTP_200_OK
        if database_ok
        else status.HTTP_503_SERVICE_UNAVAILABLE,
    )
