from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response, status
from sqlalchemy.orm import Session

from app import crud
from app.database import get_db
from app.models import Contact
from app.schemas import (
    ContactCreate,
    ContactPage,
    ContactRead,
    ContactReplace,
    ContactUpdate,
    ErrorResponse,
)

router = APIRouter(prefix="/api/v1/contacts", tags=["contacts"])

CONTACT_ID = Path(description="Identifier returned when the contact was created.", examples=[1], ge=1)

NOT_FOUND = {
    "model": ErrorResponse,
    "description": "No contact exists with that id.",
    "content": {"application/json": {"example": {"detail": "Contact 42 not found"}}},
}
EMAIL_CONFLICT = {
    "model": ErrorResponse,
    "description": "Another contact already uses that email address.",
    "content": {"application/json": {"example": {"detail": "Email ada@example.com is already in use"}}},
}


def _get_or_404(db: Session, contact_id: int) -> Contact:
    contact = crud.get_contact(db, contact_id)
    if contact is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Contact {contact_id} not found")
    return contact


def _reject_duplicate_email(db: Session, email: str, *, exclude_id: int | None = None) -> None:
    existing = crud.get_contact_by_email(db, email)
    if existing is not None and existing.id != exclude_id:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Email {email} is already in use")


@router.post(
    "",
    response_model=ContactRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="createContact",
    summary="Create a contact",
    response_description="The stored contact, including its new id and timestamps.",
    responses={status.HTTP_409_CONFLICT: EMAIL_CONFLICT},
)
def create_contact(payload: ContactCreate, db: Session = Depends(get_db)) -> Contact:
    """
    Store a new contact.

    `first_name`, `last_name`, and `email` are required; every other field is
    optional. The email must be unique — a duplicate (compared case-insensitively)
    is rejected with `409 Conflict` rather than creating a second record.
    """
    _reject_duplicate_email(db, payload.email)
    return crud.create_contact(db, payload)


@router.get(
    "",
    response_model=ContactPage,
    operation_id="listContacts",
    summary="List contacts",
    response_description="A page of contacts plus the total number of matches.",
)
def list_contacts(
    db: Session = Depends(get_db),
    search: str | None = Query(
        default=None,
        description=(
            "Case-insensitive substring match against first name, last name, "
            "email, company, and phone. Omit to return everything."
        ),
        examples=["lovelace"],
    ),
    limit: int = Query(default=50, ge=1, le=200, description="Maximum contacts to return (1–200)."),
    offset: int = Query(default=0, ge=0, description="Number of contacts to skip, for paging."),
    sort_by: str = Query(
        default="id",
        pattern=f"^({'|'.join(crud.SORTABLE_FIELDS)})$",
        description=f"Field to sort on. One of: {', '.join(crud.SORTABLE_FIELDS)}.",
    ),
    order: str = Query(default="asc", pattern="^(asc|desc)$", description="Sort direction: `asc` or `desc`."),
) -> ContactPage:
    """
    List contacts with optional search, sorting, and pagination.

    Results are wrapped in an object rather than returned as a bare array, so
    `total` tells you how many contacts match regardless of `limit`/`offset`.
    An unrecognised `sort_by` is rejected with `422` — sort fields are validated
    against an allow-list, never interpolated into SQL.
    """
    items, total = crud.list_contacts(
        db, search=search, limit=limit, offset=offset, sort_by=sort_by, order=order
    )
    return ContactPage(
        items=[ContactRead.model_validate(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{contact_id}",
    response_model=ContactRead,
    operation_id="getContact",
    summary="Get a contact",
    response_description="The requested contact.",
    responses={status.HTTP_404_NOT_FOUND: NOT_FOUND},
)
def get_contact(contact_id: int = CONTACT_ID, db: Session = Depends(get_db)) -> Contact:
    """Fetch a single contact by its id."""
    return _get_or_404(db, contact_id)


@router.put(
    "/{contact_id}",
    response_model=ContactRead,
    operation_id="replaceContact",
    summary="Replace a contact",
    response_description="The contact after replacement.",
    responses={status.HTTP_404_NOT_FOUND: NOT_FOUND, status.HTTP_409_CONFLICT: EMAIL_CONFLICT},
)
def replace_contact(
    payload: ContactReplace,
    contact_id: int = CONTACT_ID,
    db: Session = Depends(get_db),
) -> Contact:
    """
    Replace every field of an existing contact.

    This is a true `PUT`: optional fields you leave out of the body are cleared
    to `null`. To change a subset of fields, use `PATCH` instead.
    """
    contact = _get_or_404(db, contact_id)
    _reject_duplicate_email(db, payload.email, exclude_id=contact_id)
    return crud.replace_contact(db, contact, payload)


@router.patch(
    "/{contact_id}",
    response_model=ContactRead,
    operation_id="updateContact",
    summary="Partially update a contact",
    response_description="The contact after the update.",
    responses={status.HTTP_404_NOT_FOUND: NOT_FOUND, status.HTTP_409_CONFLICT: EMAIL_CONFLICT},
)
def update_contact(
    payload: ContactUpdate,
    contact_id: int = CONTACT_ID,
    db: Session = Depends(get_db),
) -> Contact:
    """
    Update only the fields present in the request body.

    Fields you omit keep their current value. Re-sending a contact's own email
    address is allowed; using an email that belongs to a different contact
    returns `409 Conflict`.
    """
    contact = _get_or_404(db, contact_id)
    if payload.email is not None:
        _reject_duplicate_email(db, payload.email, exclude_id=contact_id)
    return crud.update_contact(db, contact, payload)


@router.delete(
    "/{contact_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="deleteContact",
    summary="Delete a contact",
    response_description="Deleted; the response has no body.",
    responses={
        status.HTTP_204_NO_CONTENT: {"description": "Deleted; the response has no body."},
        status.HTTP_404_NOT_FOUND: NOT_FOUND,
    },
)
def delete_contact(contact_id: int = CONTACT_ID, db: Session = Depends(get_db)) -> Response:
    """
    Permanently delete a contact.

    Deletion is not idempotent here: a second call for the same id returns `404`.
    """
    contact = _get_or_404(db, contact_id)
    crud.delete_contact(db, contact)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
