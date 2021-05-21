from paramiko import SSHClient
from typing import Dict
from flask import Flask, jsonify, request
from marshmallow import fields, Schema, ValidationError, post_load, validates, validate
from sqlalchemy.orm.exc import NoResultFound
from apispec import APISpec
from apispec.ext.marshmallow import MarshmallowPlugin
from apispec_webframeworks.flask import FlaskPlugin
from flask_sqlalchemy import SQLAlchemy
import json
from ipaddress import IPv4Address, AddressValueError
from create_db import Interface, Address
from connection import Connection, Iproute2Error
from config import database_path

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = database_path
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)
spec = APISpec(
    title="Network Interfaces Management Service",
    version="1.0",
    openapi_version="3.0.3",
    info=dict(description="This API allows to create, change and delete network interfaces on internal VM via ssh."),
    plugins=[FlaskPlugin(), MarshmallowPlugin()],
)


def startup(conn: Connection) -> None:
    for interface in db.session.query(Interface).all():
        try:
            conn.ip_link_add(interface)
        except Iproute2Error as e:
            if e.args[0]['message'] == ['RTNETLINK answers: File exists\n']:
                connection.ip_link_set(interface, mtu=interface.mtu)
            else:
                raise e
        conn.set_addresses(interface)


class ConciseAddressSchema(Schema):
    id = fields.Integer(dump_only=True)
    address = fields.Str(required=True)

    @validates('address')
    def validate_ip(self, address: str):
        try:
            IPv4Address(address)
        except AddressValueError:
            raise ValidationError(f'{address} is not a valid IP address.')

    @post_load
    def init_address(self, data: Dict, **kwargs):
        return Address(**data)


class AddressSchema(ConciseAddressSchema):
    interface_id = fields.Integer(required=True)


class InterfaceSchema(Schema):
    id = fields.Integer(dump_only=True)
    name = fields.Str(required=True, validate=validate.Regexp('^[0-9A-Za-z_]+$'))
    mtu = fields.Integer(missing=1500, validate=validate.Range(0, 999999999))
    addresses = fields.List(fields.Nested(ConciseAddressSchema))

    @post_load
    def init_interface(self, data: Dict, **kwargs):
        return Interface(**{k: v for (k, v) in data.items() if k != 'addresses'})


@app.route('/interfaces', methods=['GET'])
def get_interfaces():
    """Return all interfaces.
    ---
    get:
        summary: Return all interfaces
        description: Return all interfaces created through API.
        operationId: get_interfaces
        responses:
            200:
                description: list of all interfaces created through API
                content:
                    application/json:
                        schema:
                            type: array
                            items: Interface
    """
    return interface_schema.dumps(db.session.query(Interface).all(), many=True, indent=2)


@app.route('/interfaces', methods=['POST'])
def post_interface():
    """Add interface.
    ---
    post:
        summary: Add new interface
        description: Create interface with given name and, possibly, set its mtu. Also can bind one or many \
specified IP addresses to newly created interface. Name MUST NOT match name of any existing interface, including \
system interfaces.
        operationId: post_interface
        requestBody:
            content:
                application/json:
                    schema: Interface
        responses:
            201:
                description: interface was successfully created
                content:
                    application/json:
                        schema: Interface
            400:
                description: bad request
                content:
                    application/json:
                        schema: Error
            409:
                description: interface cannot be created, because name is already in use
                content:
                    application/json:
                        schema: Error
    """
    # initialize and create interface
    interface = interface_schema.load(request.json)
    connection.ip_link_add(interface)
    db.session.add(interface)
    db.session.flush()

    try:
        # initialize and create addresses
        addresses = [Address(**data, interface_id=interface.id) for data in request.json.get('addresses', [])]
        for address in addresses:
            connection.ip_address_add(address, interface)
        db.session.add_all(addresses)
    except Exception as e:
        connection.ip_link_delete(interface)
        raise e

    db.session.commit()
    return interface_schema.dumps(interface, indent=2), 201


@app.route('/interfaces/<int:int_id>', methods=['GET'])
def get_interface(int_id: int):
    """Return selected interface.
    ---
    get:
        summary: Return selected interface
        description: Return interface with given id.
        operationId: get_interface
        parameters:
        -   in: path
            name: int_id
            description: interface id
            schema:
                 type: integer
        responses:
            200:
                description: interface with given id
                content:
                    application/json:
                        schema: Interface
            404:
                description: interface not found
                content:
                    application/json:
                        schema: Error
    """
    return interface_schema.dumps(db.session.query(Interface).filter(Interface.id == int_id).one(), indent=2)


@app.route('/interfaces/<int:int_id>', methods=['PUT'])
def put_interface(int_id: int):  # TODO: requestBody schema (partial=True)
    """Change interface.
    ---
    put:
        summary: Change interface
        description: Change name and/or mtu of interface previously created through API. If a list of addresses \
is passed, deletes addresses outside of this list and creates missing addresses. Name MUST NOT match name of any \
existing interface, including system interfaces.
        operationId: put_interface
        requestBody:
            content:
                application/json:
                    schema: Interface
        parameters:
        -   in: path
            name: int_id
            description: interface id
            schema:
                 type: integer
        responses:
            200:
                description: interface was successfully changed
                content:
                    application/json:
                        schema: Interface
            400:
                description: bad request
                content:
                    application/json:
                        schema: Error
            404:
                description: interface not found
                content:
                    application/json:
                        schema: Error
            409:
                description: interface cannot be changed, because name is already in use
                content:
                    application/json:
                        schema: Error
    """
    interface = db.session.query(Interface).filter_by(id=int_id).one()

    # change interface
    interface_schema.load(request.json, partial=True)
    connection.ip_link_set(interface, name=request.json.get('name'), mtu=request.json.get('mtu'))
    interface.name = request.json.get('name', interface.name)
    interface.mtu = request.json.get('mtu', interface.mtu)

    # delete/create addresses
    if 'addresses' in request.json:
        interface.addresses = [Address(**data) for data in request.json['addresses']]
        db.session.add_all(interface.addresses)
        connection.set_addresses(interface)

    db.session.commit()
    return interface_schema.dumps(interface, indent=2)


@app.route('/interfaces/<int:int_id>', methods=['DELETE'])
def delete_interface(int_id: int):
    """Delete interface.
    ---
    delete:
        summary: Delete interface
        description: Delete interface previously created through API.
        operationId: delete_interface
        parameters:
        -   in: path
            name: int_id
            description: interface id
            schema:
                 type: integer
        responses:
            204:
                description: interface deleted
            404:
                description: interface not found
                content:
                    application/json:
                        schema: Error
    """
    interface = db.session.query(Interface).filter_by(id=int_id).one()
    connection.ip_link_delete(interface)
    db.session.delete(interface)
    db.session.commit()
    return '', 204


@app.route('/addresses', methods=['GET'])
def get_addresses():
    """Return all IP addresses.
    ---
    get:
        summary: Return all IP addresses
        description: Return all IP addresses assigned to some interfaces through API.
        operationId: get_addresses
        responses:
            200:
                description: list of all addresses assigned through API
                content:
                    application/json:
                        schema:
                            type: array
                            items: Address
    """
    return address_schema.dumps(db.session.query(Address).all(), many=True, indent=2)


@app.route('/addresses', methods=['POST'])
def post_addresses():
    """Assign address.
    ---
    post:
        summary: Assign address
        description: Assign address to a previously created interface.
        operationId: post_addresses
        requestBody:
            content:
                application/json:
                    schema: Address
        responses:
            201:
                description: address successfully assigned
                content:
                    application/json:
                        schema: Address
            400:
                description: bad request
                content:
                    application/json:
                        schema: Error
            404:
                description: interface not found
                content:
                    application/json:
                        schema: Error
            409:
                description: this address is already assigned to this interface
                content:
                    application/json:
                        schema: Error
    """
    address = address_schema.load(request.json)
    connection.ip_address_add(address, db.session.query(Interface).filter_by(id=address.interface_id).one())
    db.session.add(address)
    db.session.commit()
    return address_schema.dumps(address, indent=2), 201


@app.route('/addresses/<int:addr_id>', methods=['GET'])
def get_address(addr_id: int):
    """Return selected address.
    ---
    get:
        summary: Return selected address
        description: Return address with given id.
        operationId: get_address
        parameters:
        -   in: path
            name: addr_id
            description: address id
            schema:
                 type: integer
        responses:
            200:
                description: address with given id
                content:
                    application/json:
                        schema: Address
            404:
                description: address not found
                content:
                    application/json:
                        schema: Error
    """
    return address_schema.dumps(db.session.query(Address).filter_by(id=addr_id).one(), indent=2)


@app.route('/addresses/<int:addr_id>', methods=['DELETE'])
def delete_address(addr_id: int):
    """Delete address.
    ---
    delete:
        summary: Delete address
        description: Delete address previously assigned through API.
        operationId: delete_address
        parameters:
        -   in: path
            name: addr_id
            description: address id
            schema:
                 type: integer
        responses:
            204:
                description: address deleted
            404:
                description: address not found
                content:
                    application/json:
                        schema: Error
    """
    address = db.session.query(Address).filter_by(id=addr_id).one()
    connection.ip_address_delete(address, db.session.query(Interface).filter_by(id=address.interface_id).one())
    db.session.delete(address)
    db.session.commit()
    return '', 204


@app.route('/interfaces/<int:int_id>/addresses', methods=['GET'])
def get_addresses_by_interface(int_id: int):
    """Return addresses by interface.
    ---
    get:
        summary: Return addresses by interface
        description: Return all IP addresses assigned to given interface through API.
        operationId: get_addresses_by_interface
        parameters:
        -   in: path
            name: int_id
            description: interface id
            schema:
                 type: integer
        responses:
            200:
                description: all IP addresses assigned to given interface through API
                content:
                    application/json:
                        schema:
                            type: array
                            items: ConciseAddress
            404:
                description: interface not found
                content:
                    application/json:
                        schema: Error
    """
    db.session.query(Interface).filter_by(id=int_id).one()
    return concise_address_schema.dumps(db.session.query(Address).filter_by(interface_id=int_id).all(),
                                        many=True, indent=2)


@app.route('/interfaces/<int:int_id>/addresses', methods=['POST'])
def post_addresses_by_interface(int_id: int):
    """Assign address.
    ---
    post:
        summary: Assign address
        description: Assign address to given interface.
        operationId: post_addresses_by_interface
        requestBody:
            content:
                application/json:
                    schema: Address
        parameters:
        -   in: path
            name: int_id
            description: interface id
            schema:
                 type: integer
        responses:
            201:
                description: address successfully assigned
                content:
                    application/json:
                        schema: ConciseAddress
            400:
                description: bad request
                content:
                    application/json:
                        schema: Error
            404:
                description: interface not found
                content:
                    application/json:
                        schema: Error
            409:
                description: this address is already assigned to this interface
                content:
                    application/json:
                        schema: Error
    """
    address = concise_address_schema.load(request.json)
    address.interface_id = int_id
    connection.ip_address_add(address, db.session.query(Interface).filter_by(id=int_id).one())
    db.session.add(address)
    db.session.commit()
    return concise_address_schema.dumps(address, indent=2), 201


@app.route('/interfaces/<int:int_id>/addresses/<int:addr_id>', methods=['GET'])
def get_address_by_interface(int_id: int, addr_id: int):
    """Return selected address.
    ---
    get:
        summary: Return selected address
        description: Return address with given id assigned to interface with given id.
        operationId: get_address_by_interface
        parameters:
        -   in: path
            name: int_id
            description: interface id
            schema:
                 type: integer
        -   in: path
            name: addr_id
            description: address id
            schema:
                 type: integer
        responses:
            200:
                description: address with given id
                content:
                    application/json:
                        schema: ConciseAddress
            404:
                description: address not found or interface_id of address does not match given int_id
                content:
                    application/json:
                        schema: Error
    """
    return concise_address_schema.dumps(db.session.query(Address).
                                        filter_by(interface_id=int_id).
                                        filter_by(id=addr_id).one(), indent=2)


@app.route('/interfaces/<int:int_id>/addresses/<int:addr_id>', methods=['DELETE'])
def delete_address_by_interface(int_id: int, addr_id: int):
    """Delete address.
    ---
    delete:
        summary: Delete address
        description: Delete address previously assigned through API to given interface.
        operationId: delete_address_by_interface
        parameters:
        -   in: path
            name: int_id
            description: interface id
            schema:
                 type: integer
        -   in: path
            name: addr_id
            description: address id
            schema:
                 type: integer
        responses:
            204:
                description: address deleted
            404:
                description: address not found or interface_id of address does not match given int_id
                content:
                    application/json:
                        schema: Error
    """
    address = db.session.query(Address).filter_by(interface_id=int_id).filter_by(id=addr_id).one()
    connection.ip_address_delete(address, db.session.query(Interface).filter_by(id=int_id).one())
    db.session.delete(address)
    db.session.commit()
    return '', 204


@app.errorhandler(ValidationError)
def validation_error(error):
    return jsonify({'error': error.messages}), 400


@app.errorhandler(NoResultFound)
def not_found(_):
    return jsonify({'error': 'No result found.'}), 404


@app.errorhandler(Iproute2Error)
def iproute2_error(error):
    args_ = error.args[0]
    if args_['message'] == ['RTNETLINK answers: File exists\n']:
        if args_['command'].startswith('sudo ip link add'):
            return jsonify({'error': f'Interface name {args_["interface"].name} is already in use.'}), 409
        elif args_['command'].startswith('sudo ip link set'):
            return jsonify({'error': f'Interface name {args_["name"]} is already in use.'}), 409
        elif args_['command'].startswith('sudo ip address add'):
            return jsonify({'error': f'Address {args_["address"].address} is already assigned to interface \
{args_["interface"].name}. No need to assign again.'}), 409
    return jsonify({'error': str(args_)}), 500


@app.errorhandler(400)
def bad_request(_):
    return jsonify({'error': 'Bad request.'}), 400


@app.errorhandler(404)
def not_found(_):
    return jsonify({'error': 'Not found.'}), 404


@app.errorhandler(405)
def method_not_allowed(_):
    return jsonify({'error': 'Method not allowed.'}), 405


def generate_spec():
    spec.components.schema("Interface", schema=InterfaceSchema)
    spec.components.schema("Address", schema=AddressSchema)
    spec.components.schema("Error", {"properties": {"error": {"type": "string"}}})  # TODO ValidationError?
    with app.test_request_context():
        spec.path(view=get_interfaces)
        spec.path(view=get_interface)
        spec.path(view=post_interface)
        spec.path(view=put_interface)
        spec.path(view=delete_interface)
        spec.path(view=get_address)
        spec.path(view=get_addresses)
        spec.path(view=post_addresses)
        spec.path(view=delete_address)
        spec.path(view=get_address_by_interface)
        spec.path(view=get_addresses_by_interface)
        spec.path(view=post_addresses_by_interface)
        spec.path(view=delete_address_by_interface)
    with open('openapi.json', 'w') as f:
        f.write(json.dumps(spec.to_dict(), indent=2))


with SSHClient() as ssh:
    interface_schema = InterfaceSchema()
    address_schema = AddressSchema()
    concise_address_schema = ConciseAddressSchema()
    connection = Connection(ssh)
    startup(connection)
    generate_spec()

    if __name__ == '__main__':
        app.run(debug=True)


# TODO: IPv6 addresses

# methods:
#   GET /interfaces
#   POST /interfaces
#   GET /interfaces/1
#   PUT /interfaces/1
#   DELETE /interfaces/1
#   GET /addresses
#   POST /addresses
#   GET /addresses/1
#   DELETE /addresses/1
#   GET /interfaces/1/addresses
#   POST /interfaces/1/addresses
#   GET /interfaces/1/addresses/1
#   DELETE /interfaces/1/addresses/1
