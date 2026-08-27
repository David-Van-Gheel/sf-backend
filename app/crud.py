from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models import Address, Contact, _utcnow
from app.schemas import ContactCreate, ContactReplace, ContactUpdate

SORTABLE_FIELDS = ("id", "first_name", "last_name", "email", "company", "created_at", "updated_at")


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def get_contact(db: Session, contact_id: int) -> Contact | None:
    return db.get(Contact, contact_id)


def get_contact_by_email(db: Session, email: str) -> Contact | None:
    stmt = select(Contact).where(func.lower(Contact.email) == _normalize_email(email))
    return db.execute(stmt).scalar_one_or_none()


def count_contacts(db: Session) -> int:
    return db.execute(select(func.count()).select_from(Contact)).scalar_one()


ADDRESS_FIELDS = ("address", "city", "state", "postal_code", "country")


def _address_values(address: Address) -> dict[str, str | None]:
    return {field: getattr(address, field) for field in ADDRESS_FIELDS}


def _sync_legacy_address(contact: Contact) -> None:
    values = _address_values(contact.addresses[0]) if contact.addresses else dict.fromkeys(ADDRESS_FIELDS)
    for field, value in values.items():
        setattr(contact, field, value)


def list_contacts(
    db: Session,
    *,
    search: str | None = None,
    limit: int = 50,
    offset: int = 0,
    sort_by: str = "id",
    order: str = "asc",
) -> tuple[list[Contact], int]:
    """Return (page of contacts, total matching count)."""
    stmt = select(Contact)

    if search:
        pattern = f"%{search.strip().lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(Contact.first_name).like(pattern),
                func.lower(Contact.last_name).like(pattern),
                func.lower(Contact.email).like(pattern),
                func.lower(func.coalesce(Contact.company, "")).like(pattern),
                func.lower(func.coalesce(Contact.phone, "")).like(pattern),
            )
        )

    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()

    if sort_by not in SORTABLE_FIELDS:
        sort_by = "id"
    column = getattr(Contact, sort_by)
    stmt = stmt.order_by(column.desc() if order == "desc" else column.asc())

    items = db.execute(stmt.limit(limit).offset(offset)).scalars().all()
    return list(items), total


def create_contact(db: Session, payload: ContactCreate) -> Contact:
    data = payload.model_dump()
    addresses = data.pop("addresses", [])
    data["email"] = _normalize_email(data["email"])
    contact = Contact(**data)
    if addresses:
        contact.addresses = [Address(**address) for address in addresses]
    elif any(data.get(field) is not None for field in ADDRESS_FIELDS):
        contact.addresses = [Address(type="Other", **{field: data.get(field) for field in ADDRESS_FIELDS})]
    _sync_legacy_address(contact)
    contact.updated_at = _utcnow()
    db.add(contact)
    db.commit()
    db.refresh(contact)
    return contact


def replace_contact(db: Session, contact: Contact, payload: ContactReplace) -> Contact:
    data = payload.model_dump()
    addresses = data.pop("addresses", [])
    for field, value in data.items():
        setattr(contact, field, _normalize_email(value) if field == "email" else value)
    contact.addresses = [Address(**address) for address in addresses]
    _sync_legacy_address(contact)
    contact.updated_at = _utcnow()
    db.commit()
    db.refresh(contact)
    return contact


def update_contact(db: Session, contact: Contact, payload: ContactUpdate) -> Contact:
    data = payload.model_dump(exclude_unset=True)
    addresses_supplied = "addresses" in payload.model_fields_set
    addresses = data.pop("addresses", None)
    address_updates = {field: data.pop(field) for field in ADDRESS_FIELDS if field in data}
    for field, value in data.items():
        setattr(contact, field, _normalize_email(value) if field == "email" else value)
    if addresses_supplied:
        contact.addresses = [Address(**address) for address in (addresses or [])]
        _sync_legacy_address(contact)
        contact.updated_at = _utcnow()
    elif address_updates:
        if contact.addresses:
            primary = contact.addresses[0]
            for field, value in address_updates.items():
                setattr(primary, field, value)
        else:
            primary = Address(type="Other", **{field: getattr(contact, field) for field in ADDRESS_FIELDS})
            for field, value in address_updates.items():
                setattr(primary, field, value)
            contact.addresses = [primary]
        _sync_legacy_address(contact)
        contact.updated_at = _utcnow()
    db.commit()
    db.refresh(contact)
    return contact


def delete_contact(db: Session, contact: Contact) -> None:
    db.delete(contact)
    db.commit()
