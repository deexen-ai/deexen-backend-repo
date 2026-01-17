from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# MySQL connection
DATABASE_URL = "mysql+pymysql://root:882004@localhost:3306/deexendemo"

engine = create_engine(
    DATABASE_URL, connect_args={"charset": "utf8mb4"}
)

SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()
