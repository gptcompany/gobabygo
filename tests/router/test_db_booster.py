import pytest
from src.router.db import RouterDB
from src.router.models import Worker

@pytest.fixture
def db(tmp_path):
    db_path = str(tmp_path / "booster.db")
    db = RouterDB(db_path, check_same_thread=False)
    db.init_schema()
    yield db
    db.close()

@pytest.mark.parametrize("worker_id, status", [
    ("w1", "idle"),
    ("w2", "busy"),
    ("w3", "offline"),
])
def test_insert_worker_parameterized(db, worker_id, status):
    worker = Worker(worker_id=worker_id, cli_type="claude", account_profile="work", status=status)
    db.insert_worker(worker)
    fetched = db.get_worker(worker_id)
    assert fetched.worker_id == worker_id
    assert fetched.status == status
