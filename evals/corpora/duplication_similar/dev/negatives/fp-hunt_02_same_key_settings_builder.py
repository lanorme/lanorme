def build_smtp_settings(opts):
    settings = {}
    settings["host"] = opts.mail_host
    settings["port"] = opts.mail_port
    settings["secure"] = opts.mail_use_tls
    settings["timeout"] = opts.mail_timeout
    settings["retries"] = opts.mail_max_retries
    return settings


def build_storage_settings(opts):
    settings = {}
    settings["host"] = opts.bucket_endpoint
    settings["port"] = opts.bucket_port
    settings["secure"] = opts.bucket_force_ssl
    settings["timeout"] = opts.bucket_read_timeout
    settings["retries"] = opts.bucket_max_attempts
    return settings
