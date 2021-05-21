from sqlalchemy import Column, Integer, String, create_engine, ForeignKey
from sqlalchemy.orm import relationship, backref
from sqlalchemy.ext.declarative import declarative_base
from config import database_path

Base = declarative_base()


class Interface(Base):
    __tablename__ = 'interfaces'

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    mtu = Column(Integer, nullable=False)

    def __repr__(self):
        return f"<Interface(#{self.id}, {self.name}, {self.mtu})>"


class Address(Base):
    __tablename__ = 'addresses'

    id = Column(Integer, primary_key=True)
    address = Column(String, nullable=False)
    interface_id = Column(Integer, ForeignKey("interfaces.id"))

    interface = relationship(Interface, backref=backref("addresses", cascade="delete-orphan, delete"))

    def __repr__(self):
        return f"<Address(#{self.id}, {self.address}, dev #{self.interface_id})>"


if __name__ == '__main__':
    engine = create_engine(database_path)
    Base.metadata.create_all(engine)
