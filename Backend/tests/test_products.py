"""Unit and functional tests for the Products feature with Knowledge Base file handling.

Run: python tests/test_products.py
"""
import asyncio
import io

import conftest_stub  # noqa: F401
from conftest_stub import Base, SessionLocalAdmin, engine
from fastapi import UploadFile

from domain.models import Client  # noqa: E402
from LeadAI import models  # noqa: E402
from LeadAI.rbac import ROLE_PERMISSIONS, Principal  # noqa: E402
from LeadAI.routers import products  # noqa: E402

for _table in Base.metadata.sorted_tables:
    try:
        _table.create(bind=engine, checkfirst=True)
    except Exception:  # noqa: BLE001
        pass


def setup():
    db = SessionLocalAdmin()
    client = Client(Name="Nexa Finserv")
    db.add(client)
    db.flush()
    principal = Principal(
        email="admin@nexa.test",
        role="company_admin",
        client_id=client.Id,
        permissions=set(ROLE_PERMISSIONS["company_admin"]),
    )
    return db, client, principal


def test_create_product_without_file():
    db, client, principal = setup()
    out = asyncio.run(
        products.create_product(
            request=None,
            product_name="Personal Loan Express",
            product_type="Financial / Loan",
            file=None,
            principal=principal,
            db=db,
        )
    )

    assert out.product_name == "Personal Loan Express"
    assert out.product_type == "Financial / Loan"
    assert out.knowledge_base_file is None
    assert out.kb_document_id is None
    assert out.created_by == "admin@nexa.test"
    assert out.is_deleted is False

    # Check database persistence
    row = db.get(models.LeadProduct, out.id)
    assert row is not None
    assert row.ProductName == "Personal Loan Express"
    assert row.ProductType == "Financial / Loan"
    assert row.ClientId == client.Id
    assert row.CreatedBy == "admin@nexa.test"
    assert row.CreatedAt is not None
    assert row.IsDeleted is False


def test_create_product_with_kb_file():
    db, client, principal = setup()
    sample_text = (
        "Personal Loan Express features instant approval up to 10 Lakhs. "
        "Interest rate starts at 10.5% per annum with flexible tenure from 12 to 60 months. "
        "Zero foreclosure charges after 6 months of regular EMI repayment."
    )
    file_bytes = sample_text.encode("utf-8")
    upload_file = UploadFile(
        file=io.BytesIO(file_bytes),
        filename="personal_loan_guide.txt",
        headers={"content-type": "text/plain"},
    )

    out = asyncio.run(
        products.create_product(
            request=None,
            product_name="Personal Loan Express Gold",
            product_type="Financial / Loan",
            file=upload_file,
            principal=principal,
            db=db,
        )
    )

    assert out.product_name == "Personal Loan Express Gold"
    assert out.knowledge_base_file == "personal_loan_guide.txt"
    assert out.kb_document_id is not None

    # Verify Knowledge Base Document was indexed
    kb_doc = db.get(models.LeadKbDocument, out.kb_document_id)
    assert kb_doc is not None
    assert kb_doc.Status == "indexed"
    assert kb_doc.FileName == "personal_loan_guide.txt"
    assert "product" in (kb_doc.Tags or "")
    assert kb_doc.ClientId == client.Id


def test_list_and_filter_products():
    db, client, principal = setup()
    # Add two products
    asyncio.run(
        products.create_product(
            request=None,
            product_name="Home Loan Premier",
            product_type="Mortgage",
            file=None,
            principal=principal,
            db=db,
        )
    )
    asyncio.run(
        products.create_product(
            request=None,
            product_name="Credit Card Titanium",
            product_type="Cards",
            file=None,
            principal=principal,
            db=db,
        )
    )

    # List all
    all_res = products.list_products(search=None, product_type=None, principal=principal, db=db)
    names = [p.product_name for p in all_res.items]
    assert "Home Loan Premier" in names
    assert "Credit Card Titanium" in names

    # Filter by search
    search_res = products.list_products(search="Titanium", product_type=None, principal=principal, db=db)
    assert len(search_res.items) == 1
    assert search_res.items[0].product_name == "Credit Card Titanium"

    # Filter by type
    type_res = products.list_products(search=None, product_type="Mortgage", principal=principal, db=db)
    assert len(type_res.items) == 1
    assert type_res.items[0].product_name == "Home Loan Premier"


def test_update_product():
    db, client, principal = setup()
    p = asyncio.run(
        products.create_product(
            request=None,
            product_name="Old Name",
            product_type="Old Type",
            file=None,
            principal=principal,
            db=db,
        )
    )

    updated = asyncio.run(
        products.update_product(
            product_id=p.id,
            request=None,
            product_name="Updated Name",
            product_type="Updated Type",
            file=None,
            principal=principal,
            db=db,
        )
    )

    assert updated.product_name == "Updated Name"
    assert updated.product_type == "Updated Type"
    assert updated.updated_by == "admin@nexa.test"
    assert updated.updated_at is not None


def test_delete_product_soft_deletes_and_cleans_kb():
    db, client, principal = setup()
    file_bytes = b"Some product knowledge details for deletion test"
    upload_file = UploadFile(
        file=io.BytesIO(file_bytes),
        filename="deletion_test.txt",
        headers={"content-type": "text/plain"},
    )
    p = asyncio.run(
        products.create_product(
            request=None,
            product_name="To Delete",
            product_type="Temporary",
            file=upload_file,
            principal=principal,
            db=db,
        )
    )
    kb_id = p.kb_document_id
    assert kb_id is not None

    del_res = products.delete_product(product_id=p.id, request=None, principal=principal, db=db)
    assert del_res["success"] is True

    # Product is soft-deleted
    row = db.get(models.LeadProduct, p.id)
    assert row.IsDeleted is True

    # Linked KB document is soft-deleted
    kb_row = db.get(models.LeadKbDocument, kb_id)
    assert kb_row.IsDeleted is True

    # List products should no longer return it
    listed = products.list_products(search="To Delete", product_type=None, principal=principal, db=db)
    assert len(listed.items) == 0


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
