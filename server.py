#!/usr/bin/env python
#
# Lara Maia <dev@lara.click> 2019
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
import os
import socket
import ssl
import subprocess

import aiohttp
import requests
from sanic import response, Blueprint, Sanic

server = Sanic()
server_home = '/home/alarm/Develop/server'
config = configparser.RawConfigParser()
config.read(os.path.join(server_home, 'config.ini'))

lara_click = Blueprint("lara.click", host='lara.click')
lara_click_home = '/home/alarm/Develop/lara.click'
lara_click.static('/', lara_click_home)

api = Blueprint('api', host='api.lara.click')


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


@lara_click.route('/', methods=['GET'])
def lara_click_index(request):
    index_args = {
        'server_status_1': is_online('192.168.0.101'),
        'server_status_2': is_online('192.168.0.102'),
        'server_status_3': is_online('192.168.0.103'),
        'server_status_4': is_online('192.168.0.104'),
        'server_status_5': is_online('192.168.0.105'),
        'server_status_6': is_online('192.168.0.106'),
    }

    with open(os.path.join(lara_click_home, 'index.html'), 'r') as index:
        html = index.read().format(**index_args)

    return response.html(html)


@api.route('/', methods=['GET', 'POST'])
def api_index(request):
    return response.text('404 Not Found!', status=404)


@api.route('/<path:[^/].*?>', methods=['GET', 'POST'])
async def api_proxy(request, path):
    token = "?"

    if request.query_string:
        token = '&'

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


server.blueprint(lara_click)
server.blueprint(api)


class ListDNS(argparse.Action):
    def __init__(self, **kwargs):
        super().__init__(nargs=0, **kwargs)

    def __call__(self, *args, **kwargs):
        api_server = 'https://api.cloudflare.com'
        CF_Key = config.get('CloudFlare', 'CF_Key')
        CF_Email = config.get('CloudFlare', 'CF_Email')
        CF_ID = config.get('CloudFlare', 'CF_ID')
        CF_Type = config.get('CloudFlare', 'CF_Type')

        headers = {
            'X-Auth-Email': CF_Email,
            'X-Auth-Key': CF_Key,
        }

        with requests.get(
                f'{api_server}/client/v4/zones/{CF_ID}/dns_records?type={CF_Type}',
                headers=headers
        ) as response_:
            print(json.dumps(response_.json(), indent=4))


class StartServer(argparse.Action):
    def __init__(self, **kwargs):
        super().__init__(nargs=0, **kwargs)

    def __call__(self, *args, **kwargs):
        ssl_context = ssl.create_default_context(purpose=ssl.Purpose.CLIENT_AUTH)
        ssl_directory = config.get('General', 'ssl_directory')
        ssl_context.load_cert_chain(
            os.path.join(ssl_directory, 'fullchain.cer'),
            keyfile=os.path.join(ssl_directory, 'lara.click.key'),
        )

        ipv6_socket = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        ipv6_socket.bind(('::', 443))

        server.run(sock=ipv6_socket, ssl=ssl_context, debug=False, access_log=True)


class IssueCert(argparse.Action):
    def __init__(self, **kwargs):
        super().__init__(nargs=0, **kwargs)

    def __call__(self, *args, **kwargs):
        env = {
            'HOME': '/home/alarm',
            'CF_Key': config.get('CloudFlare', 'CF_Key'),
            'CF_Email': config.get('CloudFlare', 'CF_Email'),
        }

        try:
            subprocess.check_call(['acme.sh', '--issue', '-d', 'lara.click', '--dns', 'dns_cf'], env=env)
        except subprocess.CalledProcessError as exception:
            if exception.returncode != 2 and exception.returncode != 0:
                raise exception


class UpdateDns(argparse.Action):
    headers = {
        'X-Auth-Email': config.get('CloudFlare', 'CF_Email'),
        'X-Auth-Key': config.get('CloudFlare', 'CF_Key'),
        'Content-Type': 'application/json'
    }

    def __init__(self, **kwargs):
        super().__init__(nargs=0, **kwargs)
        self.zone_id = config.get('CloudFlare', 'CF_ID')

    def list(self, type_='AAAA'):
        url = f"https://api.cloudflare.com/client/v4/zones/{self.zone_id}/dns_records"

        payload = {
            'type': type_,
        }

        response_ = requests.get(url, headers=self.headers, data=json.dumps(payload))

        return response_.json()

    @staticmethod
    def _get_local_address():
        return socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET6)[0][4][0]

    def _get_remote_address(self):
        return self.list()['result'][0]['content']

    def update(self, ip, type='AAAA', proxied=True):
        url = f"https://api.cloudflare.com/client/v4/zones/{self.zone_id}/dns_records/{id}"

        payload = {
            'type': type,
            'name': 'lara.click',
            'proxied': proxied,
            'content': ip,
        }

        response_ = requests.put(url, headers=self.headers, data=json.dumps(payload))

        return response_.status_code

    def __call__(self, *args, **kwargs):
        local_ip = self._get_local_address()
        remote_ip = self._get_remote_address()

        if local_ip == remote_ip:
            print("Address is already updated")
        else:
            print(self.update(local_ip))


if __name__ == '__main__':
    command_parser = argparse.ArgumentParser()
    command_parser.add_argument('--list-dns', action=ListDNS)
    command_parser.add_argument('--start', action=StartServer)
    command_parser.add_argument('--issue-cert', action=IssueCert)
    command_parser.add_argument('--update-dns', action=UpdateDns)
    command_parser.parse_args()
