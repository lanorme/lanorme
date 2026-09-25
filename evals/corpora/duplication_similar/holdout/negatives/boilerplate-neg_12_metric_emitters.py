def emit_request_metrics(client, request, duration):
    client.gauge("http.request.duration", duration)
    client.increment("http.request.count")
    client.tag("method", request.method)
    client.tag("route", request.route)
    client.tag("status", request.status_code)
    client.flush()


def emit_job_metrics(client, job, duration):
    client.gauge("worker.job.duration", duration)
    client.increment("worker.job.count")
    client.tag("queue", job.queue_name)
    client.tag("task", job.task_name)
    client.tag("outcome", job.outcome)
    client.flush()
