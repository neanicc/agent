from app.models import User


def load_user(user_id: str) -> User:
    return User.get(user_id)
