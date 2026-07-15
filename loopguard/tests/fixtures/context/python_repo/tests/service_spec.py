from src.service import load_user


def test_load_user():
    assert load_user("1")
