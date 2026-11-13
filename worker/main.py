import functools
import socket
import threading
import traceback

from common import broker
from worker.handler import handle

def on_message(conn, ch, method, properties, body):
    tag = method.delivery_tag

    def work():
        try:
            handle(body)
            done = functools.partial(ch.basic_ack, tag)
        except Exception:
            traceback.print_exc()
            done = functools.partial(ch.basic_nack, tag, requeue=True)
        conn.add_callback_threadsafe(done)

    threading.Thread(target=work, daemon=True).start()

def main():
    conn, ch = broker.connect()
    ch.basic_qos(prefetch_count=1)
    ch.basic_consume(broker.JOBS, functools.partial(on_message, conn))
    print(f"worker {socket.gethostname()} waiting for jobs")
    ch.start_consuming()

if __name__ == "__main__":
    main()
