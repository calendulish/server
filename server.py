#!/usr/bin/env python
#
# Lara Maia <dev@lara.click> 2019~2021
#
# Why?! Because I WANT!! Shut Up!!!
#
# server.py is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# server.py is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
#

import argparse
import configparser
import json
import netifaces
import os
import socket
import ssl
import subprocess

from typing import Any, Dict, List, Optional, Tuple

import aiohttp
import requests
from sanic import response, Blueprint, Sanic

script_dir = os.path.abspath(os.path.dirname(__file__))
server = Sanic("Cascavel Server")
config = configparser.RawConfigParser()
config.read(os.path.join(script_dir, 'config.ini'))

lara_monster = Blueprint("lara.monster", host='lara.monster')
lara_monster_home = '/home/pi/lara.monster'
lara_monster.static('/', lara_monster_home)
api = Blueprint('api', host='api.lara.monster')


@server.listener('before_server_start')
def init(server_, loop):
    server_.aiohttp_session = aiohttp.ClientSession(loop=loop)


@server.listener('after_server_stop')
def quit(server_, loop):
    loop.run_until_complete(server_.session.close())
    loop.close()


def is_online(ip):
    try:
        subprocess.check_call(['ping', '-c', '1', '-W', '1', ip])
    except subprocess.CalledProcessError:
        return 'Offline'
    else:
        return 'Online'


@api.route('/', methods=['GET', 'POST'])
def api_index(request):
    return response.text('404 Not Found!', status=404)


@api.route('/<path:[^/].*?>', methods=['GET', 'POST'])
async def api_proxy(request, path):
    token = '&' if request.query_string else "?"
    kwargs = {
        'method': 'POST' if request.form else 'GET',
        'url': f"{config.get('General', 'steam_server')}/{path}",
        'params': {**request.raw_args, **{'key': config.get('General', 'steam_key')}},
        'data': request.form,
        'headers': {'User-agent': 'Unknown/0.0.0'},
    }

    # noinspection PyUnresolvedReferences
    async with server.aiohttp_session.request(**kwargs) as proxy_response:
        if proxy_response.status != 200:
            return response.text('Nop', status=proxy_response.status)

        return response.json(await proxy_response.json())


server.blueprint(lara_monster)
server.blueprint(api)


class StartServer(argparse.Action):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)

    def __call__(self, *args, **kwargs) -> None:
        ssl_context = ssl.create_default_context(purpose=ssl.Purpose.CLIENT_AUTH)
        ssl_directory = config.get('General', 'ssl_directory')

        ssl_context.load_cert_chain(
            os.path.join(ssl_directory, 'fullchain.cer'),
            keyfile=os.path.join(ssl_directory, 'lara.monster.key'),
        )

        ipv6_socket = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        ipv6_socket.bind(('::', 443))

        server.run(sock=ipv6_socket, ssl=ssl_context, debug=False, access_log=True)


class Cloudflare:
    def __init__(self, mail: str, key: str, api_server: str = 'https://api.cloudflare.com') -> None:
        self.api_server = api_server
        self.mail = mail
        self.key = key

    @staticmethod
    def _get_local_address(interface: str, type_: Tuple[int]):
        #return subprocess.check_output(['curl', 'ifconfig.me']).decode()
        #return socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET6)[0][4][0]
        return netifaces.ifaddresses(interface)[type_[0]][type_[1]]['addr']

    def _get_remote_address(self, zone_id: str, type_: str):
        dns_list = self.list_dns(zone_id, type_)
        return dns_list['result'][0]['content']

    def list_dns(self, zone_id: str, type_: str) -> Dict[str, Any]:
        headers = {
            'X-Auth-Email': self.mail,
            'X-Auth-Key': self.key,
        }

        with requests.get(
            f'{self.api_server}/client/v4/zones/{zone_id}/dns_records?type={type_}',
            headers=headers
        ) as response_:
            return response_.json()

    def update_dns(
        self,
        name: str,
        zone_id: str,
        record_id: str,
        remote_type: str,
        local_type: Tuple[int],
        interface: str = 'eth0',
        proxied: bool = True,
    ) -> Optional[requests.Request]:
        local_address = self._get_local_address(interface, local_type)
        remote_address = self._get_remote_address(zone_id, remote_type)
        
        if local_address == remote_address:
            return

        headers = {
            'X-Auth-Email': self.mail,
            'X-Auth-Key': self.key,
            'Content-Type': 'application/json',
        }

        payload = {
            'type': remote_type,
            'name': name,
            'proxied': proxied,
            'content': local_address,
        }

        with requests.put(
            f"{self.api_server}/client/v4/zones/{zone_id}/dns_records/{record_id}",
            headers=headers,
            data=json.dumps(payload),
        ) as response_:
            return response_

    def issue_cert(self, ssl_home: str, acme_directory: str, domains: List[str]) -> None:
        env = {
            'HOME': ssl_home,
            'CF_Key': self.key,
            'CF_Email': self.mail,
        }

        acme = os.path.join(script_dir, acme_directory, 'acme.sh')
        kwargs = [acme, '--issue', '--dns', 'dns_cf']

        for domain in domains:
            kwargs.extend(['-d', domain])

        try:
            subprocess.check_call(kwargs, env=env)
        except subprocess.CalledProcessError as exception:
            if exception.returncode not in [2, 0]:
                raise exception


class CloudflareAction(argparse.Action):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        mail = config.get('Cloudflare', 'mail')
        key = config.get('Cloudflare', 'key')
        self.cloudflare = Cloudflare(mail, key)
            
    def __call__(self, parser: Any, namespace: argparse.Namespace, values: List[str], option_string: str) -> None:
        zone_id = config.get('Cloudflare', 'zone_id')

        if option_string == '--issue-cert':
            ssl_home = config.get('General', 'ssl_home')
            acme_directory = config.get('General', 'acme_directory')
            domains = ['lara.monster', 'www.lara.monster', 'api.lara.monster']
            self.cloudflare.issue_cert(ssl_home, acme_directory, domains)
        elif option_string == '--list-dns':
            dns_list = self.cloudflare.list_dns(zone_id, values[0])
            print(json.dumps(dns_list, indent=4))

        elif option_string == '--update-dns':
            if values[0] == 'main':
                record_id = config.get('Cloudflare', 'main_record_id')
                remote_type = config.get('Cloudflare', 'main_record_type')
                name = 'lara.monster'
                local_type = (netifaces.AF_INET6, 1)
                proxied = True
            else:
                record_id = config.get('Cloudflare', 'ssh_record_id')
                remote_type = config.get('Cloudflare', 'ssh_record_type')
                name = 'ssh.lara.monster'
                local_type = (netifaces.AF_INET, 0)
                proxied = False

            response = self.cloudflare.update_dns(name, zone_id, record_id, remote_type, local_type, 'wlan0', proxied)

            if not response:
                print("Address is already updated")
                return

            print(f'status: {response.status_code}')


if __name__ == '__main__':
    command_parser = argparse.ArgumentParser()
    command_parser.add_argument('--start', action=StartServer, nargs=0)
    command_parser.add_argument('--list-dns', action=CloudflareAction, nargs=1, choices=['A', 'AAAA'])
    command_parser.add_argument('--update-dns', action=CloudflareAction, nargs=1, choices=['main', 'ssh'])
    command_parser.add_argument('--issue-cert', action=CloudflareAction, nargs=0)
    command_parser.parse_args()
