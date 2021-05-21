from os import environ

server_address = environ.get('NIMS_SERVER_ADDRESS', '192.168.122.178')
server_username = environ.get('NIMS_SERVER_USERNAME', 'iluha')
database_path = environ.get('NIMS_DATABASE_PATH', 'sqlite:///interfaces.db')
