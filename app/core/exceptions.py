from fastapi import HTTPException, status


def not_found(resource: str, resource_id: int) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{resource} {resource_id} was not found")
