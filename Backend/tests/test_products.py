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
            product_description="Financial / Loan",
            file=None,
            principal=principal,
            db=db,
        )
    )

    assert out.product_name == "Personal Loan Express"
    assert out.product_description == "Financial / Loan"
    assert out.knowledge_base_file is None
    assert out.kb_document_id is None
    assert out.created_by == "admin@nexa.test"
    assert out.is_deleted is False

    # Check database persistence
    row = db.get(models.LeadProduct, out.id)
    assert row is not None
    assert row.ProductName == "Personal Loan Express"
    assert row.ProductDescription == "Financial / Loan"
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
            product_description="Financial / Loan",
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
            product_description="Mortgage",
            file=None,
            principal=principal,
            db=db,
        )
    )
    asyncio.run(
        products.create_product(
            request=None,
            product_name="Credit Card Titanium",
            product_description="Cards",
            file=None,
            principal=principal,
            db=db,
        )
    )

    # List all
    all_res = products.list_products(search=None, principal=principal, db=db)
    names = [p.product_name for p in all_res.items]
    assert "Home Loan Premier" in names
    assert "Credit Card Titanium" in names

    # Filter by search
    search_res = products.list_products(search="Titanium", principal=principal, db=db)
    assert len(search_res.items) == 1
    assert search_res.items[0].product_name == "Credit Card Titanium"

    # Filter by description search
    type_res = products.list_products(search="Mortgage", principal=principal, db=db)
    assert len(type_res.items) == 1
    assert type_res.items[0].product_name == "Home Loan Premier"


def test_update_product():
    db, client, principal = setup()
    p = asyncio.run(
        products.create_product(
            request=None,
            product_name="Old Name",
            product_description="Old Description",
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
            product_description="Updated Description",
            file=None,
            principal=principal,
            db=db,
        )
    )

    assert updated.product_name == "Updated Name"
    assert updated.product_description == "Updated Description"
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
            product_description="Temporary",
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
    listed = products.list_products(search="To Delete", principal=principal, db=db)
    assert len(listed.items) == 0


def test_bind_existing_kb_to_product():
    from LeadAI.routers.knowledge import _index
    from LeadAI.schemas import BindExistingKbRequest

    db, client, principal = setup()

    # 1. Create a product with initial KB
    initial_file = UploadFile(
        file=io.BytesIO(b"Commercial plot base specifications and layout details."),
        filename="plot_specs.txt",
        headers={"content-type": "text/plain"},
    )
    p = asyncio.run(
        products.create_product(
            request=None,
            product_name="Commercial Plot Sector 50",
            product_description="Real Estate / Property",
            file=initial_file,
            principal=principal,
            db=db,
        )
    )
    assert p.kb_document_id is not None
    assert len(p.bound_kb_documents) == 1
    assert p.bound_kb_documents[0].is_primary is True

    # 2. Index a separate KB doc in company (e.g. payment schedule)
    payment_doc = _index(
        db,
        client.Id,
        principal,
        title="Commercial Plots 2026 Payment Schedule",
        filename="payment_schedule.txt",
        content_type="text/plain",
        source_type="upload",
        text="Flexible 36-month installment plan with 10% down payment for commercial plots.",
        tags="commercial,finance,payment",
    )

    # 3. Bind the payment KB document to the existing product
    bound_p = products.bind_existing_kb(
        product_id=p.id,
        payload=BindExistingKbRequest(kb_document_id=payment_doc.Id),
        request=None,
        principal=principal,
        db=db,
    )

    assert payment_doc.Id in bound_p.bound_kb_document_ids
    assert len(bound_p.bound_kb_documents) == 2
    # Verify primary and bound docs are reflected
    doc_titles = [d.title for d in bound_p.bound_kb_documents]
    assert "Commercial Plots 2026 Payment Schedule" in doc_titles


def test_bind_uploaded_kb_to_product():
    db, client, principal = setup()

    p = asyncio.run(
        products.create_product(
            request=None,
            product_name="Industrial Warehouse",
            product_description="Real Estate / Property",
            file=None,
            principal=principal,
            db=db,
        )
    )

    # Bind a new uploaded KB document
    additional_file = UploadFile(
        file=io.BytesIO(b"Fire safety norms and clearance certificate details for warehouse."),
        filename="safety_clearance.txt",
        headers={"content-type": "text/plain"},
    )
    bound_p = asyncio.run(
        products.bind_kb(
            product_id=p.id,
            request=None,
            kb_document_id=None,
            file=additional_file,
            principal=principal,
            db=db,
        )
    )

    assert len(bound_p.bound_kb_documents) >= 1
    assert any("safety_clearance.txt" in (d.file_name or "") for d in bound_p.bound_kb_documents)


def test_unbind_kb_from_product():
    from LeadAI.routers.knowledge import _index
    from LeadAI.schemas import BindExistingKbRequest

    db, client, principal = setup()

    p = asyncio.run(
        products.create_product(
            request=None,
            product_name="Executive Office Suite",
            product_description="Real Estate / Property",
            file=None,
            principal=principal,
            db=db,
        )
    )

    extra_doc = _index(
        db,
        client.Id,
        principal,
        title="Parking Rules and Passes",
        filename="parking.txt",
        content_type="text/plain",
        source_type="upload",
        text="Reserved underground parking bay with 24/7 EV charging stations.",
        tags="office,amenities",
    )

    # Bind
    products.bind_existing_kb(
        product_id=p.id,
        payload=BindExistingKbRequest(kb_document_id=extra_doc.Id),
        request=None,
        principal=principal,
        db=db,
    )

    # Unbind
    unbound_p = products.unbind_kb(
        product_id=p.id,
        kb_document_id=extra_doc.Id,
        request=None,
        principal=principal,
        db=db,
    )
    assert extra_doc.Id not in unbound_p.bound_kb_document_ids


def test_create_and_update_product_description():
    db, client, principal = setup()
    out = asyncio.run(
        products.create_product(
            request=None,
            product_name="Executive Health Plan",
            product_description="Comprehensive family medical coverage up to $500,000 with zero copay.",
            file=None,
            principal=principal,
            db=db,
        )
    )

    assert out.product_name == "Executive Health Plan"
    assert out.product_description == "Comprehensive family medical coverage up to $500,000 with zero copay."
    assert out.id is not None

    # Check database persistence
    row = db.get(models.LeadProduct, out.id)
    assert row is not None
    assert row.ProductName == "Executive Health Plan"
    assert row.ProductDescription == "Comprehensive family medical coverage up to $500,000 with zero copay."

    # Update product description
    updated = asyncio.run(
        products.update_product(
            product_id=out.id,
            request=None,
            product_name=None,
            product_description="Updated coverage with dental and vision add-on.",
            file=None,
            principal=principal,
            db=db,
        )
    )
    assert updated.product_description == "Updated coverage with dental and vision add-on."

    # Verify listing search matches description
    search_res = products.list_products(search="dental", principal=principal, db=db)
    assert search_res.total >= 1
    assert any(item.id == out.id for item in search_res.items)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
