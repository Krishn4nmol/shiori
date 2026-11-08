"""Accounts, sessions, roles and data isolation, using real login cookies."""

import uuid
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.auth.rate_limit import MAX_FAILURES
from app.auth.security import COOKIE_NAME, create_session_token, read_session_token
from app.db import SessionLocal, engine
from app.main import app
from app.models import Conversation, Document

pytestmark = pytest.mark.usefixtures("real_auth")

ADMIN = {"email": "Admin@Lab.test", "name": "Prof. Admin", "password": "correct horse 1"}
MEMBER = {"email": "student@lab.test", "name": "Student", "password": "student pass 1"}


@pytest.fixture
async def empty_db(database: None) -> None:
    async with engine.begin() as db:
        await db.execute(
            text(
                "TRUNCATE users, documents, chunks, conversations, messages, traces, "
                "eval_questions, eval_runs, eval_results CASCADE"
            )
        )


def new_client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.fixture
async def admin(empty_db: None) -> AsyncIterator[AsyncClient]:
    async with new_client() as c:
        assert (await c.post("/api/auth/setup", json=ADMIN)).status_code == 201
        yield c


@pytest.fixture
async def member(admin: AsyncClient) -> AsyncIterator[AsyncClient]:
    assert (await admin.post("/api/users", json=MEMBER)).status_code == 201
    async with new_client() as c:
        res = await c.post(
            "/api/auth/login", json={"email": MEMBER["email"], "password": MEMBER["password"]}
        )
        assert res.status_code == 200
        yield c


# --- tokens ---------------------------------------------------------------------------


def test_session_token_round_trip_and_tampering() -> None:
    user_id = uuid.uuid4()
    token = create_session_token(user_id, 3)
    claims = read_session_token(token)
    assert claims and claims.user_id == user_id and claims.session_version == 3
    assert read_session_token(token[:-2] + "xx") is None
    assert read_session_token("not-a-token") is None


# --- first-run setup --------------------------------------------------------------------


async def test_setup_creates_admin_once(empty_db: None) -> None:
    async with new_client() as c:
        assert (await c.get("/api/auth/status")).json() == {"setup_required": True}
        res = await c.post("/api/auth/setup", json=ADMIN)
        assert res.status_code == 201
        assert res.json()["role"] == "admin"
        assert res.json()["email"] == "admin@lab.test"  # normalised
        assert (await c.get("/api/auth/me")).json()["name"] == "Prof. Admin"  # logged in
        assert (await c.get("/api/auth/status")).json() == {"setup_required": False}

    async with new_client() as other:
        res = await other.post("/api/auth/setup", json={**ADMIN, "email": "evil@x.test"})
        assert res.status_code == 409


async def test_setup_adopts_data_created_before_accounts(empty_db: None) -> None:
    async with SessionLocal() as session:
        session.add(Conversation(title="old question"))
        session.add(
            Document(title="old", filename="o.pdf", sha256="a" * 64, file_path="/x", size_bytes=1)
        )
        await session.commit()
    async with new_client() as c:
        await c.post("/api/auth/setup", json=ADMIN)
        titles = [conv["title"] for conv in (await c.get("/api/conversations")).json()]
        assert titles == ["old question"]
        docs = (await c.get("/api/documents")).json()
        assert docs[0]["uploaded_by_id"] == (await c.get("/api/auth/me")).json()["id"]


async def test_setup_rejects_weak_password_and_bad_email(empty_db: None) -> None:
    async with new_client() as c:
        assert (
            await c.post("/api/auth/setup", json={**ADMIN, "password": "short"})
        ).status_code == 422
        assert (await c.post("/api/auth/setup", json={**ADMIN, "email": "nope"})).status_code == 422


# --- login and sessions -----------------------------------------------------------------


async def test_everything_but_login_requires_a_session(empty_db: None) -> None:
    async with new_client() as c:
        assert (await c.get("/api/health")).status_code == 200
        assert (await c.get("/api/auth/status")).status_code == 200
        for method, path in [
            ("GET", "/api/documents"),
            ("POST", "/api/search"),
            ("POST", "/api/chat"),
            ("GET", "/api/conversations"),
            ("POST", "/api/llm/check"),
            ("GET", "/api/users"),
            ("GET", "/api/auth/me"),
        ]:
            res = await c.request(method, path, json={"query": "x", "content": "x"})
            assert res.status_code == 401, (method, path)


async def test_login_sets_a_secure_cookie(admin: AsyncClient) -> None:
    async with new_client() as c:
        res = await c.post(
            "/api/auth/login", json={"email": "ADMIN@lab.test", "password": ADMIN["password"]}
        )
        assert res.status_code == 200
        cookie = res.headers["set-cookie"]
        assert cookie.startswith(f"{COOKIE_NAME}=")
        assert "HttpOnly" in cookie and "SameSite=lax" in cookie
        assert "password_hash" not in res.text


async def test_wrong_password_and_unknown_email_look_the_same(admin: AsyncClient) -> None:
    async with new_client() as c:
        wrong = await c.post(
            "/api/auth/login", json={"email": ADMIN["email"], "password": "nope nope"}
        )
        unknown = await c.post(
            "/api/auth/login", json={"email": "who@lab.test", "password": "nope nope"}
        )
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()


async def test_repeated_failures_are_rate_limited(admin: AsyncClient) -> None:
    async with new_client() as c:
        for _ in range(MAX_FAILURES):
            await c.post(
                "/api/auth/login", json={"email": ADMIN["email"], "password": "guess guess"}
            )
        res = await c.post(
            "/api/auth/login", json={"email": ADMIN["email"], "password": ADMIN["password"]}
        )
    assert res.status_code == 429
    assert int(res.headers["retry-after"]) > 0


async def test_logout_ends_the_session(admin: AsyncClient) -> None:
    assert (await admin.post("/api/auth/logout")).status_code == 204
    assert (await admin.get("/api/auth/me")).status_code == 401


async def test_password_change_logs_out_other_sessions(admin: AsyncClient) -> None:
    async with new_client() as laptop:
        await laptop.post(
            "/api/auth/login", json={"email": ADMIN["email"], "password": ADMIN["password"]}
        )
        res = await admin.post(
            "/api/auth/password",
            json={"current_password": ADMIN["password"], "new_password": "brand new pass"},
        )
        assert res.status_code == 204
        assert (await admin.get("/api/auth/me")).status_code == 200  # this session continues
        assert (await laptop.get("/api/auth/me")).status_code == 401  # the other one ends

    bad = await admin.post(
        "/api/auth/password", json={"current_password": "wrong one", "new_password": "x" * 10}
    )
    assert bad.status_code == 400


# --- roles ------------------------------------------------------------------------------


async def test_members_cannot_manage_users(member: AsyncClient) -> None:
    assert (await member.get("/api/users")).status_code == 403
    assert (
        await member.post("/api/users", json={**MEMBER, "email": "x@lab.test"})
    ).status_code == 403


async def test_admin_manages_users(admin: AsyncClient, member: AsyncClient) -> None:
    users = (await admin.get("/api/users")).json()
    student = next(u for u in users if u["email"] == MEMBER["email"])
    assert student["role"] == "member"

    dup = await admin.post("/api/users", json=MEMBER)
    assert dup.status_code == 409

    res = await admin.patch(f"/api/users/{student['id']}", json={"is_active": False})
    assert res.json()["is_active"] is False
    assert (await member.get("/api/auth/me")).status_code == 401  # kicked out immediately
    async with new_client() as c:
        login = await c.post(
            "/api/auth/login", json={"email": MEMBER["email"], "password": MEMBER["password"]}
        )
        assert login.status_code == 401

    await admin.patch(
        f"/api/users/{student['id']}", json={"is_active": True, "password": "reset pass 1"}
    )
    async with new_client() as c:
        login = await c.post(
            "/api/auth/login", json={"email": MEMBER["email"], "password": "reset pass 1"}
        )
        assert login.status_code == 200


async def test_last_admin_cannot_be_removed(admin: AsyncClient) -> None:
    me = (await admin.get("/api/auth/me")).json()
    res = await admin.patch(f"/api/users/{me['id']}", json={"role": "member"})
    assert res.status_code == 400
    res = await admin.patch(f"/api/users/{me['id']}", json={"is_active": False})
    assert res.status_code == 400


# --- data isolation ---------------------------------------------------------------------


async def test_conversations_are_private(admin: AsyncClient, member: AsyncClient) -> None:
    async with SessionLocal() as session:
        admin_id = uuid.UUID((await admin.get("/api/auth/me")).json()["id"])
        conv = Conversation(title="admin's private question", user_id=admin_id)
        session.add(conv)
        await session.commit()
        conv_id = conv.id

    assert (await member.get("/api/conversations")).json() == []
    assert (await member.get(f"/api/conversations/{conv_id}")).status_code == 404
    assert (await member.delete(f"/api/conversations/{conv_id}")).status_code == 404
    res = await member.post("/api/chat", json={"content": "hi", "conversation_id": str(conv_id)})
    assert res.status_code == 404
    assert len((await admin.get("/api/conversations")).json()) == 1


async def test_only_uploader_or_admin_can_delete_papers(
    admin: AsyncClient, member: AsyncClient
) -> None:
    admin_id = uuid.UUID((await admin.get("/api/auth/me")).json()["id"])
    async with SessionLocal() as session:
        doc = Document(
            title="admin paper",
            filename="a.pdf",
            sha256="b" * 64,
            file_path="/nonexistent/a.pdf",
            size_bytes=1,
            uploaded_by_id=admin_id,
            status="ready",
        )
        session.add(doc)
        await session.commit()
        doc_id = doc.id

    assert (await member.get(f"/api/documents/{doc_id}")).status_code == 200  # shared library
    assert (await member.delete(f"/api/documents/{doc_id}")).status_code == 403
    assert (await member.post(f"/api/documents/{doc_id}/reingest")).status_code == 403
    assert (await admin.delete(f"/api/documents/{doc_id}")).status_code == 204
