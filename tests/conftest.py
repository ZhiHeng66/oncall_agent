import os

# Unit and contract tests must not read developer credentials from the local .env file.
os.environ["ONCALL_DISABLE_ENV_FILE"] = "1"
