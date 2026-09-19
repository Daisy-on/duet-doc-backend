from alibabacloud_oss_v2.exceptions import OperationError, ServiceError

from app.services.oss_client import oss_service_error


def test_oss_service_error_unwraps_operation_error() -> None:
    service_error = ServiceError(
        status_code=403,
        code="AccessDenied",
        request_id="test",
        message="Denied",
        ec="",
        timestamp="",
        request_target="",
    )

    assert (
        oss_service_error(OperationError(name="DeleteObject", error=service_error)) is service_error
    )
    assert oss_service_error(RuntimeError("network")) is None
