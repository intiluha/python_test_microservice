from paramiko import SSHClient
from typing import Optional, List, Tuple
from create_db import Interface, Address
from config import server_address, server_username


class Iproute2Error(Exception):
    pass


class Connection:
    def __init__(self, ssh_: SSHClient):
        ssh_.load_system_host_keys()
        ssh_.connect(server_address, username=server_username)
        self.ssh = ssh_

    def list_all_interface_names(self) -> List[str]:
        command = 'ip link show'
        _, out, err = self.ssh.exec_command(command)
        message = err.readlines()
        if message:
            raise Iproute2Error({'command': command, 'message': message})
        return [line.split()[1][:-1] for line in out.readlines()[::2]]

    def ip_link_add(self, interface: Interface) -> None:
        command = f'sudo ip link add {interface.name}'
        if interface.mtu:
            command += f' mtu {interface.mtu}'
        command += ' type dummy'
        message = self.ssh.exec_command(command)[2].readlines()
        if message:
            raise Iproute2Error({'command': command, 'message': message, 'interface': interface})

    def ip_link_set(self, interface: Interface, name: Optional[str] = None, mtu: Optional[int] = None) -> None:
        command = f'sudo ip link set dev {interface.name}'
        if name:
            command += f' name {name}'
        if mtu:
            command += f' mtu {mtu}'
        message = self.ssh.exec_command(command)[2].readlines()
        if message:
            raise Iproute2Error(
                {'command': command, 'message': message, 'interface': interface, 'name': name, 'mtu': mtu})

    def ip_link_delete(self, interface: Interface) -> None:
        command = f'sudo ip link delete dev {interface.name} type dummy'
        message = self.ssh.exec_command(command)[2].readlines()
        if message:
            raise Iproute2Error({'command': command, 'message': message, 'interface': interface})

    def ip_address_add(self, address: Address, interface: Interface) -> None:
        command = f'sudo ip address add dev {interface.name} local {address.address}'
        message = self.ssh.exec_command(command)[2].readlines()
        if message:
            raise Iproute2Error({'command': command, 'message': message, 'interface': interface, 'address': address})

    def _ip_address_delete(self, addr: str, interface: Interface) -> None:
        command = f'sudo ip address delete dev {interface.name} local {addr}/32'
        message = self.ssh.exec_command(command)[2].readlines()
        if message:
            raise Iproute2Error({'command': command, 'message': message, 'interface': interface, 'address': addr})

    def ip_address_delete(self, address: Address, interface: Interface) -> None:
        try:
            self._ip_address_delete(address.address, interface)
        except Iproute2Error as e:
            e.args[0]['address'] = address
            raise e

    def ip_address_show(self, interface: Interface) -> Tuple[int, List[str]]:
        command = f'ip address show dev {interface.name} type dummy'
        _, out, err = self.ssh.exec_command(command)
        message = err.readlines()
        if message:
            raise Iproute2Error({'command': command, 'message': message, 'interface': interface})
        mtu = int(out.readline().split()[4])
        addrs = [x.split()[1].split('/')[0] for x in out.readlines()[1:-1:2]]
        return mtu, addrs

    def set_addresses(self, interface: Interface) -> None:  # TODO: this should be done better?
        for addr in self.ip_address_show(interface)[1]:
            self._ip_address_delete(addr, interface)
        for address in interface.addresses:
            self.ip_address_add(address, interface)
