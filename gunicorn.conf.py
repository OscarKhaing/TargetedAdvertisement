# gunicorn reads this file from its working directory. Ports and worker count are in boogle.service.
worker_class = 'gthread'
threads = 8         # /ad spends most of its time waiting on DSPs, so don't tie up a worker
preload_app = True  # load the model once, before forking, so both workers share that memory


def post_worker_init(worker):
    # Each worker has its own connections, so each warms up as soon as it starts.
    import ssp
    ssp.warm_connections()
