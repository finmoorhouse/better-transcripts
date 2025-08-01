# FastAPI Users + SQLModel

You will notice that the [SQLModel](https://sqlmodel.tiangolo.com/) example is very similar to the [SQLAlchemy](#sqlalchemy) example for 
[fastapi-users](https://github.com/fastapi-users/fastapi-users). 
This is because SQLModel is built on top of SQLAlchemy and pydantic.

There are a few important differences you should take note of:

#### `app/db.py`

- Removing the `DeclarativeBase` SQLAlchemy base class.
- Using `fastapi_users.db.SQLModelBaseUserDB` instead of
  `fastapi_users.db.SQLAlchemyBaseUserTable`.
- Using `fastapi_users.db.SQLModelUserDatabaseAsync` instead of
  `fastapi_users.db.SQLAlchemyUserDatabase`.
- Setting the `class_` parameter of `sessionmaker` to `AsyncSession`.
- Using `SQLModel.metadata.create_all` instead of `Base.metadata.create_all`.

#### `app/users.py`

- Using `fastapi_users.db.SQLModelUserDatabaseAsync` instead of
  `fastapi_users.db.SQLAlchemyUserDatabase`.