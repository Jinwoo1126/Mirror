import os
import uuid

import pytest

from mirror.extract import MockExtractor
from mirror.store import MirrorStore

# Set to run the same store tests against Postgres, e.g.
#   MIRROR_TEST_PG_DSN=postgresql://postgres:...@127.0.0.1:54329/mirror
PG_DSN = os.environ.get("MIRROR_TEST_PG_DSN")


def new_pg_user(dsn: str) -> str:
    import psycopg
    uid = str(uuid.uuid4())
    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute("insert into auth.users (id) values (%s)", (uid,))
    return uid


def drop_pg_user(dsn: str, uid: str) -> None:
    import psycopg
    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute("delete from auth.users where id = %s", (uid,))  # cascades


@pytest.fixture(params=["kuzu", "postgres"])
def store(request, tmp_path):
    if request.param == "kuzu":
        st = MirrorStore(str(tmp_path / "test.kuzu"))
        yield st
        st.close()
        return
    if not PG_DSN:
        pytest.skip("set MIRROR_TEST_PG_DSN to test the Postgres store")
    from mirror.pg_store import PostgresStore
    uid = new_pg_user(PG_DSN)
    st = PostgresStore(PG_DSN, uid)
    yield st
    st.close()
    drop_pg_user(PG_DSN, uid)


@pytest.fixture
def mock():
    return MockExtractor()


DINNER_TEXT = (
    "10월 16일 목요일 저녁 8시. 야근으로 피곤해서 새로운 식당을 찾아보기 "
    "귀찮았다. 결국 항상 시키는 익숙한 집에 배달을 주문했다."
)
