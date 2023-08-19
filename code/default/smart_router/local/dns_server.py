#!/usr/bin/env python
# coding:utf-8

import os
import sys
import threading
import socket
import time
import select

current_path = os.path.dirname(os.path.abspath(__file__))
root_path = os.path.abspath(os.path.join(current_path, os.pardir, os.pardir))
top_path = os.path.abspath(os.path.join(root_path, os.pardir, os.pardir))

python_path = root_path
noarch_lib = os.path.join(python_path, 'lib', 'noarch')
sys.path.append(noarch_lib)

import env_info
data_path = os.path.join(env_info.data_path, 'smart_router')

import utils
from dnslib import DNSRecord, DNSHeader, A, AAAA, RR, DNSQuestion, QTYPE, NS

from . import global_var as g
from xlog import getLogger
xlog = getLogger("smart_router")


class DnsServer(object):
    def __init__(self, bind_ip="127.0.0.1", port=53, backup_port=8053, ttl=24*3600):
        self.sockets = []
        self.listen_port = port
        self.running = False
        if isinstance(bind_ip, str):
            self.bind_ip = [bind_ip]
        else:
            # server can listen multi-port
            self.bind_ip = bind_ip
        self.port = port
        self.backup_port = backup_port
        self.ttl = ttl
        self.th = None

    def init_socket(self):
        ips = set(self.bind_ip)
        listen_all_v4 = "0.0.0.0" in ips
        listen_all_v6 = "::" in ips
        for ip in ips:
            if ip not in ("0.0.0.0", "::") and (
                    listen_all_v4 and '.' in ip or
                    listen_all_v6 and ':' in ip):
                continue
            self.bing_listen(ip)

    def bing_listen(self, bind_ip):
        if ":" in bind_ip:
            sock = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
        else:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        try:
            sock.bind((bind_ip, self.port))
            xlog.info("start DNS server at %s:%d", bind_ip, self.port)
            self.running = True
            self.sockets.append(sock)
            return
        except:
            xlog.warn("bind DNS %s:%d fail", bind_ip, self.port)
            pass

        try:
            sock.bind((bind_ip, self.backup_port))
            xlog.info("start DNS server at %s:%d", bind_ip, self.backup_port)
            self.running = True
            self.listen_port = self.backup_port
            self.sockets.append(sock)
            return
        except Exception as e:
            xlog.warn("bind DNS %s:%d fail", bind_ip, self.backup_port)

            if sys.platform.startswith("linux"):
                xlog.warn("You can try: install libcap2-bin")
                xlog.warn("Then: sudo setcap 'cap_net_bind_service=+ep' /usr/bin/python2.7")
                xlog.warn("Or run as root")

    def on_udp_query(self, rsock, req_data, addr):
        start_time = time.time()
        try:
            request = DNSRecord.parse(req_data)
            if len(request.questions) != 1:
                xlog.warn("query num:%d %s", len(request.questions), request)
                return

            domain = utils.to_bytes(str(request.questions[0].qname))

            if domain.endswith(b"."):
                domain = domain[:-1]

            dns_type = request.questions[0].qtype
            xlog.debug("DNS query:%s type:%d from %s", domain, dns_type, addr)

            ips = g.dns_query.query(domain, dns_type)
            if not ips:
                xlog.debug("query:%s type:%d from:%s, get fail, cost:%d", domain, dns_type, addr,
                           (time.time() - start_time) * 1000)

            reply = DNSRecord(DNSHeader(id=request.header.id, qr=1, aa=1, ra=1, auth=1), q=request.q)
            ips = utils.to_bytes(ips)
            for ip_cn in ips:
                ipcn_p = ip_cn.split(b"|")
                ip = ipcn_p[0]
                if utils.check_ip_valid4(ip) and dns_type == 1:
                    reply.add_answer(RR(domain, ttl=60, rdata=A(ip)))
                elif utils.check_ip_valid6(ip) and dns_type == 28:
                    reply.add_answer(RR(domain, rtype=dns_type, ttl=60, rdata=AAAA(ip)))
                elif dns_type == 2:
                    reply.add_answer(RR(domain, rtype=dns_type, ttl=60, rdata=NS(ip)))
            res_data = reply.pack()

            rsock.sendto(res_data, addr)
            xlog.debug("query:%s type:%d from:%s, return ip num:%d cost:%d", domain, dns_type, addr,
                       len(reply.rr), (time.time()-start_time)*1000)
        except Exception as e:
            xlog.exception("on_query except:%r", e)

    def server_forever(self):
        while self.running:
            r, w, e = select.select(self.sockets, [], [], 1)
            for rsock in r:
                if not self.running:
                    break

                try:
                    data, addr = rsock.recvfrom(1024)
                except Exception as e:
                    xlog.warn("recv except: %r", e)
                    break

                threading.Thread(target=self.on_udp_query, args=(rsock, data, addr), name="DNSServer_udp_handler").start()

        self.th = None

    def start(self):
        self.init_socket()
        self.th = threading.Thread(target=self.server_forever, name="DNSServer")
        self.th.start()

    def stop(self):
        self.running = False
        while self.th:
            time.sleep(1)
        for sock in self.sockets:
            sock.close()
        self.sockets = []
        xlog.info("dns_server stop")


