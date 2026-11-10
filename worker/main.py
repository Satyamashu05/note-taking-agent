import socket

import pika

from common import config

def main():
    conn = pika.BlockingConnection(pika.URLParameters(config.RABBITMQ_URL))
    print(f"worker {socket.gethostname()} connected")
    while True:
        conn.process_data_events(time_limit=5)

if __name__ == "__main__":
    main()
