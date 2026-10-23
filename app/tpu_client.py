"""Клиент relay: JPEG -> детекции через RabbitMQ. pika не потокобезопасна, поэтому соединением
владеет один фоновый поток, а submit() из любых потоков кладёт задачу в очередь и получает Future."""
import json
import queue
import threading
import time
import uuid
from concurrent.futures import Future

import pika


class RelayClient:
    def __init__(self, host, port, login):
        self.params = pika.ConnectionParameters(host, port, credentials=pika.PlainCredentials('guest', 'guest'),
                                                heartbeat=60, blocked_connection_timeout=30)
        self.relay_queue = f'{login}.relay'
        self.outbox = queue.Queue()
        self.futures = {}
        self.error = None
        threading.Thread(target=self._loop, daemon=True, name='relay-client').start()

    def submit(self, jpeg: bytes, **headers) -> Future:
        fut = Future()
        fut.sent_at = time.perf_counter()
        self.outbox.put((jpeg, headers, fut))
        return fut

    def ping(self, timeout=5.0):
        return self.submit(b'', type='ping').result(timeout)

    def _loop(self):
        while True:
            try:
                conn = pika.BlockingConnection(self.params)
                ch = conn.channel()
                reply_queue = ch.queue_declare(queue='', exclusive=True).method.queue
                ch.basic_consume(reply_queue, self._on_reply, auto_ack=True)
                self.error = None
                while True:
                    while not self.outbox.empty():
                        jpeg, headers, fut = self.outbox.get_nowait()
                        corr = uuid.uuid4().hex
                        self.futures[corr] = fut
                        ch.basic_publish('', self.relay_queue, jpeg, pika.BasicProperties(
                            reply_to=reply_queue, correlation_id=corr, headers=headers))
                    conn.process_data_events(time_limit=0.005)
            except Exception as e:  # обрыв VPN и т.п.: всем ожидающим — ошибка, переподключаемся
                self.error = f'{type(e).__name__}: {e}'
                for fut in self.futures.values():
                    if not fut.done():
                        fut.set_exception(ConnectionError(self.error))
                self.futures.clear()
                time.sleep(2)

    def _on_reply(self, ch, method, props, body):
        fut = self.futures.pop(props.correlation_id, None)
        if fut is not None and not fut.done():
            payload = json.loads(body)
            payload.setdefault('timing', {})['roundtrip_ms'] = round((time.perf_counter() - fut.sent_at) * 1000, 1)
            fut.set_result(payload)
