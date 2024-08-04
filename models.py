from sqlalchemy import (
    ForeignKey,
    Column,
    Integer,
    String,
    Table,
    Boolean,
    BigInteger,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy import create_engine
from sqlalchemy.orm import relationship, sessionmaker
from sqlalchemy import DateTime
from datetime import datetime

Base = declarative_base()

user_proposal = Table(
    "user_proposal",
    Base.metadata,
    Column("user_id", BigInteger, ForeignKey("users.id")),
    Column("proposal_id", String, ForeignKey("proposals.id")),
)


class Proposal(Base):
    __tablename__ = "proposals"

    id = Column(String, primary_key=True)
    name = Column(String)
    deadline = Column(Integer)
    message_id = Column(Integer)
    created_at = Column(DateTime, default=datetime.utcnow)


class User(Base):
    __tablename__ = "users"
    id = Column(BigInteger, primary_key=True)
    subscribed_to_all = Column(Boolean, default=False)
    subscribed_to = relationship(
        "Proposal", secondary=user_proposal, backref="subscribers"
    )


engine = create_engine("sqlite:///proposals.db")
Base.metadata.create_all(engine)
Session = sessionmaker(bind=engine)
