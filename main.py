from fastapi import FastAPI, HTTPException, Body, Query
from fastapi.openapi.utils import get_openapi
from fastapi.security import HTTPBearer
from starlette.responses import JSONResponse
from pydantic import BaseModel, ValidationError
from OpenSSL import crypto
import asyncio
import secrets
from typing import Any, List, Dict, Optional
import logging
from logging.handlers import TimedRotatingFileHandler
import gzip
import redis
from jose import JWTError, jwt
from datetime import datetime, timedelta
import json
import os
import importlib.util
import pathlib
import sys
import subprocess
import base64
import shutil
import re
import requests
from requests.exceptions import Timeout
from tenacity import retry, stop_after_attempt, wait_exponential
import xml.etree.ElementTree as ET

# Paths
CONFIG_DIR = "/app/config"
CONFIG_FILE = os.path.join(CONFIG_DIR, "splunk_eam_config.json")
LOG_DIR = "/app/logs"
LOG_FILE = os.path.join(LOG_DIR, "splunk-eam.log")
USE_EXTERNAL_CERT = os.getenv("USE_EXTERNAL_CERT", "false").lower() == "true"
EXTERNAL_CERT_FILE = os.getenv("EXTERNAL_CERT_FILE", "")
EXTERNAL_KEY_FILE = os.getenv("EXTERNAL_KEY_FILE", "")

# Paths for certificates
CERT_DIR = "/app/certs"
CERT_FILE = os.path.join(CERT_DIR, "ssl_cert.pem")
KEY_FILE = os.path.join(CERT_DIR, "ssl_key.pem")

# Path to custom scripts and Ansible directory
CUSTOM_SCRIPTS_DIR = "/app/custom/bin"
CUSTOM_ANSIBLE_DIR = "/app/custom/ansible"

# Ensure necessary directories
for directory in [
    CONFIG_DIR,
    LOG_DIR,
    CERT_DIR,
    CUSTOM_SCRIPTS_DIR,
    CUSTOM_ANSIBLE_DIR,
]:
    os.makedirs(directory, exist_ok=True)

# Default configuration
DEFAULT_CONFIG = {
    "logging_level": "INFO",
    "log_rotation": {
        "when": "midnight",
        "interval": 1,
        "backup_count": 7,
        "compress_logs": True,
    },
    "token_expiration_minutes": 43200,
}


# Function to parse redis.conf
def parse_redis_config(file_path="/app/config/redis.conf"):
    """
    Parse Redis configuration from the redis.conf file.
    Returns a dictionary of key configuration options.
    """
    config = {}
    try:
        with open(file_path, "r") as f:
            for line in f:
                line = line.strip()
                # Ignore comments and empty lines
                if line.startswith("#") or not line:
                    continue
                # Split key-value pairs
                if " " in line:
                    key, value = line.split(maxsplit=1)
                    config[key] = value
    except FileNotFoundError:
        print(f"Redis configuration file not found at {file_path}. Using defaults.")
    return config


# Load Redis configuration
redis_config = parse_redis_config()

# Extract Redis settings with fallbacks
REDIS_HOST = redis_config.get("bind", "127.0.0.1")  # Default to localhost
REDIS_PORT = int(redis_config.get("port", 6379))  # Default port 6379
REDIS_DB = 0  # Default DB index (if needed, add it to the config)

# Use a connection pool for Redis
redis_pool = redis.ConnectionPool(
    host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB, decode_responses=True
)
redis_client = redis.StrictRedis(connection_pool=redis_pool)


# Configuration schema using Pydantic
class ConfigSchema(BaseModel):
    logging_level: str
    log_rotation: dict
    token_expiration_minutes: int


class AdminPasswordUpdate(BaseModel):
    current_password: str
    new_password: str


class TokenRequest(BaseModel):
    username: str
    password: str


class TokenRevokeRequest(BaseModel):
    token: str


# Generate a self-signed SSL certificate
def generate_self_signed_cert(cert_file, key_file):
    """
    Generate a self-signed SSL certificate.
    """
    # Create a key pair
    key = crypto.PKey()
    key.generate_key(crypto.TYPE_RSA, 2048)

    # Create a self-signed certificate
    cert = crypto.X509()
    cert.get_subject().CN = "localhost"
    cert.set_serial_number(1000)
    cert.gmtime_adj_notBefore(0)
    cert.gmtime_adj_notAfter(10 * 365 * 24 * 60 * 60)  # 10 years
    cert.set_issuer(cert.get_subject())
    cert.set_pubkey(key)
    cert.sign(key, "sha256")

    # Write the key and certificate to files
    with open(cert_file, "wb") as cert_out, open(key_file, "wb") as key_out:
        cert_out.write(crypto.dump_certificate(crypto.FILETYPE_PEM, cert))
        key_out.write(crypto.dump_privatekey(crypto.FILETYPE_PEM, key))

    print(f"Generated self-signed certificate at {cert_file}")


# Ensure SSL certificates are available
def ensure_certificates():
    """
    Ensure that SSL certificates are available.
    """
    if USE_EXTERNAL_CERT:
        if not (
            os.path.exists(EXTERNAL_CERT_FILE) and os.path.exists(EXTERNAL_KEY_FILE)
        ):
            raise FileNotFoundError("External certificates are enabled but not found.")
        print("Using external certificates.")
        return EXTERNAL_CERT_FILE, EXTERNAL_KEY_FILE

    if not (os.path.exists(CERT_FILE) and os.path.exists(KEY_FILE)):
        print("No certificates found, generating self-signed certificates...")
        generate_self_signed_cert(CERT_FILE, KEY_FILE)

    return CERT_FILE, KEY_FILE


# Function to dynamically load and attach routes from custom scripts
def load_custom_routes(app: FastAPI):
    """
    Dynamically loads custom routes from Python files in the CUSTOM_SCRIPTS_DIR.
    """
    for script_path in pathlib.Path(CUSTOM_SCRIPTS_DIR).glob("*.py"):
        module_name = script_path.stem  # Get the file name without extension
        spec = importlib.util.spec_from_file_location(module_name, str(script_path))
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        try:
            spec.loader.exec_module(module)  # Load the module
            if hasattr(module, "register_routes") and callable(module.register_routes):
                # Call the register_routes function, passing the app instance
                module.register_routes(app)
                logger.info(f"Custom routes from {module_name} loaded successfully.")
            else:
                logger.warning(
                    f"Custom script {module_name} does not define 'register_routes(app)' function."
                )
        except Exception as e:
            logger.error(f"Failed to load custom script {module_name}: {e}")


# Load configuration
def load_config():
    if not os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "w") as f:
            json.dump(DEFAULT_CONFIG, f, indent=4)

    with open(CONFIG_FILE, "r") as f:
        config_data = json.load(f)

    try:
        return ConfigSchema(**config_data)
    except ValidationError as e:
        logging.error(f"Invalid configuration: {e}")
        return ConfigSchema(**DEFAULT_CONFIG)


config = load_config()

# Set logging level
logging_level = getattr(logging, config.logging_level.upper(), logging.INFO)

# Log rotation settings
rotation_settings = config.log_rotation
when = rotation_settings.get("when", "midnight")
interval = rotation_settings.get("interval", 1)
backup_count = rotation_settings.get("backup_count", 7)
compress_logs = rotation_settings.get("compress_logs", True)

# Create logger
logger = logging.getLogger("splunk-eam")
logger.setLevel(logging_level)

# Console Handler (Standard Output)
console_handler = logging.StreamHandler()
console_handler.setLevel(logging_level)
console_formatter = logging.Formatter(
    "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
console_handler.setFormatter(console_formatter)
logger.addHandler(console_handler)

# File Handler with Timed Rotation
file_handler = TimedRotatingFileHandler(
    LOG_FILE,
    when=when,
    interval=interval,
    backupCount=backup_count,
)
file_handler.setLevel(logging_level)
file_formatter = logging.Formatter(
    "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
file_handler.setFormatter(file_formatter)
logger.addHandler(file_handler)

# Compression of rotated logs
if compress_logs:

    def compress_rotated_log(source, dest):
        """Compress rotated logs automatically."""
        try:
            if not source.endswith(".log"):
                return
            gz_path = f"{source}.gz"
            with open(source, "rb") as log_file, gzip.open(gz_path, "wb") as gz_file:
                gz_file.writelines(log_file)
            os.remove(source)
            logger.info(f"Compressed log file: {source} -> {gz_path}")
        except Exception as e:
            logger.error(f"Error compressing log file '{source}': {e}")

    file_handler.rotator = compress_rotated_log

# Set default admin password if not already set
DEFAULT_ADMIN_PASSWORD = os.getenv("DEFAULT_ADMIN_PASSWORD", "password")
if not redis_client.exists("admin_password"):
    redis_client.set("admin_password", os.getenv("DEFAULT_ADMIN_PASSWORD", "password"))

# init API
app = FastAPI()

# Load custom routes at startup
load_custom_routes(app)


@app.middleware("http")
async def enforce_password_update(request, call_next):
    """
    Middleware to enforce updating the default admin password before accessing endpoints.
    """
    current_password = redis_client.get("admin_password")
    if (
        current_password == DEFAULT_ADMIN_PASSWORD
        and request.url.path != "/update_password"
    ):
        return JSONResponse(
            status_code=403,
            content={
                "detail": "Access denied. Please update the default admin password using the '/update_password' endpoint."
            },
        )

    # Proceed with the request if the password is updated or the path is `/update_password`
    response = await call_next(request)
    return response


# Token verification middleware
@app.middleware("http")
async def authenticate_request(request, call_next):
    if request.url.path not in [
        "/update_password",
        "/create_token",
        "/delete_token",
    ]:  # Exclude these endpoints
        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            # Return a more detailed error message
            return JSONResponse(
                status_code=401,
                content={
                    "detail": "Authorization token is missing or invalid. Please provide a valid token."
                },
            )

        token = auth_header.split(" ")[1]
        try:
            verify_token(token)
        except HTTPException as exc:
            # Customize the token verification error message
            return JSONResponse(
                status_code=exc.status_code,
                content={
                    "detail": "Invalid or revoked token. Please authenticate again."
                },
            )

    response = await call_next(request)
    return response


# mask sensitive parameters in logs
@app.middleware("http")
async def log_requests(request, call_next):
    # Extract URL and mask sensitive parameters
    raw_url = str(request.url)
    sanitized_url = re.sub(
        r"(splunk_password=)[^&]+", r"\1*****", raw_url
    )  # Mask all instances of the password

    # Log sanitized request details
    logger.info(f"Request: {request.method} {sanitized_url}")

    # Proceed with the request and log the response
    response = await call_next(request)
    logger.info(f"Response: {response.status_code}")
    return response


@app.post("/update_password")
def update_admin_password(request: AdminPasswordUpdate):
    if request.current_password != redis_client.get("admin_password"):
        raise HTTPException(status_code=401, detail="Current password is incorrect.")

    redis_client.set("admin_password", request.new_password)
    return {"message": "Admin password updated successfully"}


@app.post("/create_token")
def create_token(request: TokenRequest):
    stored_password = redis_client.get("admin_password")
    if request.username != "admin" or request.password != stored_password:
        raise HTTPException(status_code=401, detail="Invalid credentials.")

    token = create_access_token({"sub": "admin"})
    return {"access_token": token, "token_type": "bearer"}


@app.post("/delete_token")
def delete_token(request: TokenRevokeRequest):
    revoke_token(request.token)
    return {"message": "Token revoked successfully"}


# Directory to store stack files
DATA_DIR = "data"
MAIN_FILE = os.path.join(DATA_DIR, "stacks.json")

# Ensure data directory and main file exist
os.makedirs(DATA_DIR, exist_ok=True)
if not os.path.exists(MAIN_FILE):
    with open(MAIN_FILE, "w") as f:
        json.dump({"stacks": []}, f)


class Stack(BaseModel):
    stack_id: str
    enterprise_deployment_type: str  # "distributed" or "standalone"
    shc_cluster: Optional[bool] = None  # Optional unless distributed with SHC
    cluster_manager_node: Optional[str] = None  # Optional unless distributed
    shc_deployer_node: Optional[str] = None  # Optional unless SHC cluster
    shc_members: Optional[str] = (
        None  # Optional unless SHC cluster (comma-separated list of members)
    )
    ansible_python_interpreter: str = "/usr/bin/python3"  # Default Python interpreter
    splunk_home: Optional[str] = (
        "/opt/splunk"  # splunk_home: optional, defaults to "/opt/splunk"
    )
    splunkd_port: Optional[int] = 8089  # splunkd_port: optional, defaults to 8089
    splunk_user: Optional[str] = "splunk"  # splunk_user: optional, defaults to "splunk"
    splunk_group: Optional[str] = (
        "splunk"  # splunk_group: optional, defaults to "splunk"
    )


# Redis-based helper functions
def get_all_stacks():
    stacks = redis_client.hgetall("stacks")
    # Deserialize JSON strings into Python dictionaries
    return {
        stack_id: json.loads(stack_metadata)
        for stack_id, stack_metadata in stacks.items()
    }


def save_stack_metadata(stack_id, metadata):
    # Convert all values in metadata to strings
    metadata_str = {key: str(value) for key, value in metadata.items()}
    redis_client.hset("stacks", stack_id, json.dumps(metadata_str))
    redis_client.hmset(f"stack:{stack_id}:metadata", metadata_str)


def get_stack_metadata(stack_id):
    if not redis_client.exists(f"stack:{stack_id}:metadata"):
        raise HTTPException(status_code=404, detail="Stack not found.")
    return redis_client.hgetall(f"stack:{stack_id}:metadata")


def delete_stack_metadata(stack_id):
    redis_client.hdel("stacks", stack_id)
    redis_client.delete(f"stack:{stack_id}:metadata")
    redis_client.delete(f"stack:{stack_id}:inventory")
    redis_client.delete(f"stack:{stack_id}:indexes")
    redis_client.delete(f"stack:{stack_id}:apps")


def get_inventory(stack_id):
    if not redis_client.exists(f"stack:{stack_id}:inventory"):
        raise HTTPException(status_code=404, detail="Inventory not found.")
    return redis_client.get(f"stack:{stack_id}:inventory")


def save_inventory(stack_id, inventory_data):
    redis_client.set(f"stack:{stack_id}:inventory", inventory_data)


def get_indexes(stack_id):
    return redis_client.hgetall(f"stack:{stack_id}:indexes")


def save_index(stack_id, index_name, index_data):
    redis_client.hset(f"stack:{stack_id}:indexes", index_name, json.dumps(index_data))


def delete_index(stack_id, index_name):
    redis_client.hdel(f"stack:{stack_id}:indexes", index_name)


def get_apps(stack_id):
    return redis_client.hgetall(f"stack:{stack_id}:apps")


def save_app(stack_id, app_name, app_data):
    redis_client.hset(f"stack:{stack_id}:apps", app_name, json.dumps(app_data))


def delete_app(stack_id, app_name):
    redis_client.hdel(f"stack:{stack_id}:apps", app_name)


# Helper functions for the main file
def load_main_file():
    with open(MAIN_FILE, "r") as f:
        return json.load(f)


def save_main_file(data):
    with open(MAIN_FILE, "w") as f:
        json.dump(data, f, indent=4)


# Token Management with Redis

# Paths
AUTH_DIR = "/app/data/auth"
SECRET_KEY_FILE = os.path.join(AUTH_DIR, "splunk-eam.secret")

# Ensure data directory exists
os.makedirs(AUTH_DIR, exist_ok=True)


# Generate or load the secret key
def get_or_create_secret_key():
    """
    Generate a secure random secret key and persist it for reuse.
    """
    if not os.path.exists(SECRET_KEY_FILE):
        # Generate a secure random key
        secret_key = secrets.token_hex(32)  # 64-character hex string
        with open(SECRET_KEY_FILE, "w") as f:
            f.write(secret_key)
        print(f"Generated and saved new secret key to {SECRET_KEY_FILE}")
    else:
        # Load the existing key
        with open(SECRET_KEY_FILE, "r") as f:
            secret_key = f.read().strip()
        print(f"Loaded existing secret key from {SECRET_KEY_FILE}")
    return secret_key


# Assign the secret key
SECRET_KEY = get_or_create_secret_key()
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = config.token_expiration_minutes


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    expire = datetime.utcnow() + (
        expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

    # Store token in Redis with the appropriate expiration
    redis_client.setex(
        encoded_jwt,
        expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
        "valid",
    )
    return encoded_jwt


@app.post("/refresh_token", summary="Refresh an access token")
def refresh_token(token: str = Body(..., embed=True)):
    """
    Refresh a valid token to extend its expiration.
    """
    try:
        # Verify the existing token
        payload = verify_token(token)

        # Revoke the old token
        revoke_token(token)

        # Create a new token with the same data
        new_token = create_access_token({"sub": payload["sub"]})

        return {"access_token": new_token, "token_type": "bearer"}

    except HTTPException as e:
        logger.error(f"Error refreshing token: {e.detail}")
        raise
    except Exception as e:
        logger.error(f"Unexpected error during token refresh: {str(e)}")
        raise HTTPException(status_code=500, detail="Unable to refresh token.")


def verify_token(token: str):
    # Check if token exists in Redis
    if not redis_client.exists(token):
        raise HTTPException(
            status_code=401, detail="Token has been revoked or is invalid."
        )

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token.")


def revoke_token(token: str):
    # Remove token from Redis
    redis_client.delete(token)


# Helper function to load stack details from Redis
def load_stack_from_redis(stack_id: str):
    # Retrieve stack metadata from Redis
    stack_metadata = redis_client.hgetall(f"stack:{stack_id}:metadata")
    if not stack_metadata:
        raise HTTPException(status_code=404, detail=f"Stack '{stack_id}' not found.")

    # Convert Redis hash values back into a Python dictionary
    stack_details = {}
    for key, value in stack_metadata.items():
        try:
            stack_details[key] = json.loads(value)  # Attempt to parse JSON
        except json.JSONDecodeError:
            stack_details[key] = value  # Fallback to plain string

    # handle shc_members as a list from comma-separated string
    if "shc_members" in stack_details:
        stack_details["shc_members"] = stack_details["shc_members"].split(",")

    return stack_details


def save_stack_file(stack_id, data):
    file_path = os.path.join(DATA_DIR, f"{stack_id}.json")
    with open(file_path, "w") as f:
        json.dump(data, f, indent=4)


def ensure_stack_dir(stack_id: str):
    stack_dir = os.path.join(DATA_DIR, stack_id)
    os.makedirs(stack_dir, exist_ok=True)
    return stack_dir


def get_stack_paths(stack_id: str):
    stack_dir = os.path.join(DATA_DIR, stack_id)
    inventory_path = os.path.join(stack_dir, "inventory.ini")
    ssh_key_path = os.path.join(stack_dir, "ssh_private")
    return stack_dir, inventory_path, ssh_key_path


def get_indexes(stack_id: str) -> dict:
    """
    Retrieve all indexes for a given stack from Redis.
    """
    if not redis_client.exists(f"stack:{stack_id}:indexes"):
        return {}
    indexes = redis_client.hgetall(f"stack:{stack_id}:indexes")
    # Deserialize JSON values stored in Redis
    return {key: json.loads(value) for key, value in indexes.items()}


def save_indexes(stack_id: str, data: dict):
    """
    Save all indexes for a given stack to Redis.
    """
    redis_client.delete(f"stack:{stack_id}:indexes")  # Clear existing indexes
    for index_name, index_data in data.items():
        redis_client.hset(
            f"stack:{stack_id}:indexes", index_name, json.dumps(index_data)
        )


def ensure_stack_dir(stack_id: str):
    stack_dir = os.path.join(DATA_DIR, stack_id)
    os.makedirs(stack_dir, exist_ok=True)
    return stack_dir


async def run_ansible_playbook(
    stack_id: str,
    playbook_name: str,
    ansible_vars: dict = None,
    limit: str = None,
    creds: dict = None,
):
    # Retrieve stack metadata from Redis
    stack_metadata = redis_client.hgetall(f"stack:{stack_id}:metadata")
    if not stack_metadata:
        raise HTTPException(
            status_code=404, detail=f"Metadata for stack '{stack_id}' not found."
        )

    # Retrieve SSH private key
    stack_dir, _, ssh_key_path = get_stack_paths(stack_id)
    if not os.path.exists(ssh_key_path):
        raise HTTPException(
            status_code=400, detail=f"SSH key not found for stack '{stack_id}'."
        )

    # Retrieve inventory from Redis and create a temporary inventory file
    inventory_data = redis_client.get(f"stack:{stack_id}:inventory")
    if not inventory_data:
        raise HTTPException(
            status_code=404, detail=f"Inventory not found for stack '{stack_id}'."
        )

    temp_inventory_path = f"/tmp/{stack_id}_inventory.ini"
    try:
        with open(temp_inventory_path, "w") as f:
            f.write(inventory_data)

        # Include credentials in Ansible variables if provided
        if creds:
            if ansible_vars is None:
                ansible_vars = {}
            ansible_vars["splunk_username"] = creds["username"]
            ansible_vars["splunk_password"] = creds["password"]

        # Always add splunk_home, splunkd_port, splunk_user, and splunk_group to Ansible vars
        ansible_vars = ansible_vars or {}
        ansible_vars["splunk_home"] = stack_metadata.get("splunk_home", "/opt/splunk")
        ansible_vars["splunkd_port"] = stack_metadata.get("splunkd_port", 8089)
        ansible_vars["splunk_user"] = stack_metadata.get("splunk_user", "splunk")
        ansible_vars["splunk_group"] = stack_metadata.get("splunk_group", "splunk")

        # Prepare the Ansible playbook command
        playbook_dir = "/app/ansible"
        command = [
            "ansible-playbook",
            f"{playbook_dir}/{playbook_name}",
            "-i",
            temp_inventory_path,
            "-e",
            json.dumps(ansible_vars),
            "-e",
            "ansible_ssh_extra_args='-o StrictHostKeyChecking=no'",
            "--private-key",
            ssh_key_path,
        ]

        if limit:
            command.extend(["--limit", limit])

        # Add Python interpreter if specified in stack metadata
        ansible_python_interpreter = stack_metadata.get(
            "ansible_python_interpreter", "/usr/bin/python3"
        )
        command.extend(
            ["-e", f"ansible_python_interpreter={ansible_python_interpreter}"]
        )

        # Sanitize sensitive data in the command for logging
        sanitized_command = command[:]
        sanitized_vars = ansible_vars.copy() if ansible_vars else {}
        if "splunk_password" in sanitized_vars:
            sanitized_vars["splunk_password"] = "*****"

        sanitized_command[sanitized_command.index("-e") + 1] = json.dumps(
            sanitized_vars
        )

        sanitized_command = [
            re.sub(r"--private-key\s+[^ ]+", "--private-key *****", part)
            for part in sanitized_command
        ]

        logger.info(f"Running Ansible playbook: {sanitized_command}")

        # Run the Ansible playbook asynchronously
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout, stderr = await process.communicate()

        # Sanitize sensitive data in Ansible output
        def sanitize_output(output):
            output = re.sub(r"-auth\s+'[^:]+:[^']+'", r"-auth '*****:*****'", output)
            output = re.sub(r"--private-key\s+[^ ]+", "--private-key *****", output)
            return output

        sanitized_stdout = sanitize_output(stdout.decode())
        sanitized_stderr = sanitize_output(stderr.decode())

        logger.info(f"Ansible stdout: {sanitized_stdout}")
        if process.returncode != 0:
            logger.error(f"Ansible stderr: {sanitized_stderr}")
            raise HTTPException(
                status_code=500,
                detail=f"Ansible playbook failed: {sanitized_stderr.strip()}",
            )

    finally:
        # Cleanup temporary inventory file
        if os.path.exists(temp_inventory_path):
            os.remove(temp_inventory_path)


def login_splunkbase(username, password, proxy_dict):
    """
    Log in to Splunkbase and return the ID value from the XML response.

    Args:
        username (str): The username for Splunkbase.
        password (str): The password for Splunkbase.
        proxy_dict (dict): Proxy settings to use for the request.

    Returns:
        str: The ID value from the XML response.

    Raises:
        Exception: If the login request to Splunkbase fails.
    """
    url = "https://splunkbase.splunk.com/api/account:login"
    data = {"username": username, "password": password}

    try:
        response = requests.post(url, data=data, proxies=proxy_dict)

        if response.status_code == 200:
            xml_response = response.text
            root = ET.fromstring(xml_response)

            id_element = root.find("{http://www.w3.org/2005/Atom}id")

            if id_element is not None:
                logging.info(
                    f"Splunkbase login successful, http status code: {response.status_code}"
                )
                return id_element.text
            else:
                logging.error(
                    "Splunkbase login failed, ID element not found in the XML response"
                )
                raise Exception(
                    "Splunkbase login failed, ID element not found in the XML response"
                )
        else:
            logging.error(
                f"Splunkbase login request failed with status code {response.status_code}"
            )
            raise Exception(
                f"Splunkbase login request failed with status code {response.status_code}"
            )

    except Exception as e:
        logging.error(f"Splunkbase login failed: exception={e}")
        raise Exception("Splunkbase login failed") from e


def download_splunk_app(session_id, app_id, version, output_path):
    """
    Download a Splunk app release from Splunk Base.

    Args:
        session_id (str): Splunk Base session ID obtained from login.
        app_id (str): The ID of the app on Splunk Base.
        version (str): The version of the app to download.
        output_path (str): The local file path to save the downloaded app.

    Returns:
        str: The path to the downloaded file.

    Raises:
        HTTPException: If the download fails.
    """

    download_url = (
        f"https://splunkbase.splunk.com/app/{app_id}/release/{version}/download/"
    )
    headers = {"X-Auth-Token": session_id}

    @retry(
        stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=60)
    )
    def perform_download():
        return requests.get(
            download_url,
            headers=headers,
            stream=True,
            allow_redirects=True,
            timeout=(10, 900),
        )

    try:
        response = perform_download()

        if response.status_code != 200:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to download app. HTTP {response.status_code}: {response.text}",
            )

        content_length = int(response.headers.get("Content-Length", 0))
        bytes_written = 0

        with open(output_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
                    bytes_written += len(chunk)
                    logger.info(
                        f"Download progress: {bytes_written}/{content_length} bytes"
                    )

        logger.info(f"App downloaded successfully: {output_path}")
        return output_path

    except Timeout as e:
        logger.error(f"Download timed out: {e}")
        raise HTTPException(status_code=408, detail="Download request timed out.")
    except requests.exceptions.RequestException as e:
        logger.error(f"Error downloading Splunk app: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to download app: {str(e)}")


# Add logging middleware to capture API requests
@app.middleware("http")
async def log_requests(request, call_next):
    response = await call_next(request)
    return response


"""
Endpoint: /docs/endpoints
Description: This endpoint lists all available endpoints with their usage and options.
HttpMethod: GET
"""


# GET /docs/endpoints
@app.get("/docs/endpoints", summary="List all available endpoints")
async def list_endpoints():
    """
    This endpoint lists all available endpoints with their usage and options.
    """
    openapi_schema = get_openapi(
        title="Splunk Stack Management API",
        version="1.0.0",
        description="API for managing Splunk stacks, indexes, and apps.",
        routes=app.routes,
    )

    endpoints = []
    for path, methods in openapi_schema.get("paths", {}).items():
        for method, details in methods.items():
            endpoint = {
                "path": path,
                "method": method.upper(),
                "summary": details.get("summary", ""),
                "description": details.get("description", ""),
                "parameters": details.get("parameters", []),
                "requestBody": details.get("requestBody", {}).get("content", {}),
                "responses": details.get("responses", {}),
            }
            endpoints.append(endpoint)

    return {"endpoints": endpoints}


"""
Endpoint: /stacks
Description: This endpoint allows you to create a new Splunk stack.
HTTP Method: POST
"""


# POST /stacks
@app.get("/stacks", summary="Retrieve all stacks", response_model=Dict[str, dict])
async def get_all_stacks_endpoint():
    """
    Retrieve all available stacks.
    """
    stacks = get_all_stacks()
    return {"stacks": stacks}


"""
Endpoint: /stacks
Description: This endpoint allows you to create a new Splunk stack.
HTTP Method: POST
"""


# POST /stacks
@app.post("/stacks", summary="Create a new stack", status_code=201)
async def create_stack_endpoint(stack: Stack):
    """
    Create a new stack with the provided metadata.
    """
    # Check if the stack already exists
    existing_stacks = get_all_stacks()
    if stack.stack_id in existing_stacks:
        raise HTTPException(
            status_code=400, detail=f"Stack with ID '{stack.stack_id}' already exists."
        )

    # Validate cluster manager node for distributed deployment
    if (
        stack.enterprise_deployment_type == "distributed"
        and not stack.cluster_manager_node
    ):
        raise HTTPException(
            status_code=400,
            detail="cluster_manager_node is required for distributed deployments.",
        )

    # Validate SHC deployer node and members only if SHC cluster is enabled
    if stack.enterprise_deployment_type == "distributed" and stack.shc_cluster:
        if not stack.shc_deployer_node:
            raise HTTPException(
                status_code=400,
                detail="shc_deployer_node is required for SHC cluster setups.",
            )
        if not stack.shc_members:
            raise HTTPException(
                status_code=400,
                detail="shc_members is required for SHC cluster setups.",
            )

    # Ensure standalone stacks do not have cluster-specific fields
    if stack.enterprise_deployment_type == "standalone":
        if stack.shc_cluster or stack.shc_deployer_node or stack.shc_members:
            raise HTTPException(
                status_code=400,
                detail="Standalone stacks should not have cluster-related fields.",
            )

    # Save the new stack metadata in Redis
    metadata = stack.dict()
    save_stack_metadata(stack.stack_id, metadata)

    return {
        "message": f"Stack '{stack.stack_id}' created successfully.",
        "stack": metadata,
    }


"""
Endpoint: /stacks/{stack_id}
Description: This endpoint allows you to get, update, or delete a Splunk stack.
HTTP Methods: GET, DELETE
"""


# GET /stacks/{stack_id}
@app.get("/stacks/{stack_id}")
def get_stack(stack_id: str):
    """
    Retrieve the details of a specific stack using its stack_id.
    """
    try:
        # Retrieve stack metadata from Redis
        metadata_json = redis_client.hget("stacks", stack_id)
        if not metadata_json:
            raise HTTPException(status_code=404, detail="Stack not found.")

        # Parse the metadata JSON
        metadata = json.loads(metadata_json)
        return {"stack_id": stack_id, "metadata": metadata}

    except Exception as e:
        logger.error(f"Error retrieving stack '{stack_id}': {e}")
        raise HTTPException(
            status_code=500, detail=f"Unable to retrieve stack '{stack_id}'."
        )


# DELETE /stacks/{stack_id}
@app.delete("/stacks/{stack_id}")
def delete_stack(stack_id: str):
    """
    Delete a stack and its associated metadata.
    """
    try:
        # Check if the stack exists
        if not redis_client.hexists("stacks", stack_id):
            raise HTTPException(status_code=404, detail="Stack not found.")

        # Delete stack metadata and related Redis keys
        delete_stack_metadata(stack_id)
        logger.info(f"Stack '{stack_id}' deleted successfully.")

        return {"message": f"Stack '{stack_id}' deleted successfully."}

    except Exception as e:
        logger.error(f"Error deleting stack '{stack_id}': {e}")
        raise HTTPException(
            status_code=500, detail=f"Unable to delete stack '{stack_id}'."
        )


"""
Endpoint: /stacks/{stack_id}/inventory
Description: This endpoint allows you to get or upload the Ansible inventory file for a stack.
HTTP Methods: GET, POST
"""


# GET /stacks/{stack_id}/inventory
@app.get("/stacks/{stack_id}/inventory")
async def get_inventory_endpoint(stack_id: str):
    """
    Retrieve the Ansible inventory for a given stack.
    """
    try:
        # Fetch inventory from Redis
        inventory_data = get_inventory(stack_id)

        # Return inventory data
        return {"stack_id": stack_id, "inventory": inventory_data}

    except HTTPException as e:
        # Re-raise HTTPException (e.g., 404 from get_inventory)
        raise e
    except Exception as e:
        # Handle unexpected errors
        logger.error(f"Error retrieving inventory for stack '{stack_id}': {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Unable to retrieve inventory for stack '{stack_id}'.",
        )


# POST /stacks/{stack_id}/inventory
@app.post("/stacks/{stack_id}/inventory")
async def upload_inventory_endpoint(stack_id: str, inventory: Dict):
    """
    Upload and save the Ansible inventory for a given stack.
    """
    try:
        # Validate if the stack exists in Redis
        if not redis_client.exists(f"stack:{stack_id}:metadata"):
            raise HTTPException(status_code=404, detail="Stack not found.")

        # Convert inventory JSON to INI format
        ini_inventory = []
        for group, group_data in inventory.items():
            ini_inventory.append(f"[{group}]")
            for host, vars_dict in group_data.get("hosts", {}).items():
                vars_line = " ".join(
                    f"{key}={value}" for key, value in vars_dict.items()
                )
                ini_inventory.append(f"{host} {vars_line}")
            ini_inventory.append("")  # Add a blank line between groups

        # Join the INI lines into a single string
        ini_inventory_str = "\n".join(ini_inventory)

        # Save the inventory to Redis
        save_inventory(stack_id, ini_inventory_str)

        return {
            "message": f"Inventory for stack '{stack_id}' saved successfully",
            "stack_id": stack_id,
        }

    except HTTPException as e:
        # Re-raise HTTPException (e.g., 404 for missing stack)
        raise e
    except Exception as e:
        # Handle unexpected errors
        logger.error(f"Error saving inventory for stack '{stack_id}': {e}")
        raise HTTPException(
            status_code=500, detail=f"Unable to save inventory for stack '{stack_id}'."
        )


"""
Endpoint: /stacks/{stack_id}/ssh_key
Description: This endpoint allows you to upload the SSH private key for a stack.
HTTP Method: POST
"""


# POST /stacks/{stack_id}/ssh_key
@app.post("/stacks/{stack_id}/ssh_key")
async def upload_ssh_key(stack_id: str, ssh_key_b64: str = Body(..., embed=True)):
    # Ensure the stack directory exists
    stack_dir = ensure_stack_dir(stack_id)
    ssh_key_path = os.path.join(stack_dir, "ssh_private")

    try:
        # Decode the Base64-encoded key
        ssh_key = base64.b64decode(ssh_key_b64).decode("utf-8")

        # Save the SSH key to a file
        with open(ssh_key_path, "w") as f:
            f.write(ssh_key)

        # Set file permissions to 600
        os.chmod(ssh_key_path, 0o600)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error saving SSH key: {str(e)}")

    return {
        "message": f"SSH key for stack '{stack_id}' saved successfully",
        "path": ssh_key_path,
    }


"""
Endpoint: /stacks/{stack_id}/ansible_test
Description: This endpoint allows you to test the Ansible connection to the hosts in a stack.
HTTP Method: POST
"""


@app.post("/stacks/{stack_id}/ansible_test")
async def ansible_test(stack_id: str):
    # Retrieve the inventory from Redis
    inventory_data = redis_client.get(f"stack:{stack_id}:inventory")
    if not inventory_data:
        raise HTTPException(
            status_code=404, detail=f"Inventory not found for stack '{stack_id}'."
        )

    # Ensure SSH key exists
    stack_dir, _, ssh_key_path = get_stack_paths(stack_id)
    if not os.path.exists(ssh_key_path):
        raise HTTPException(
            status_code=400, detail=f"SSH key not found for stack '{stack_id}'."
        )

    # Temporarily save the inventory data to a file for Ansible
    temp_inventory_path = os.path.join(stack_dir, "temp_inventory.ini")
    try:
        with open(temp_inventory_path, "w") as f:
            f.write(inventory_data)

        # Run Ansible command
        command = [
            "ansible",
            "-m",
            "ping",
            "all",
            "-i",
            temp_inventory_path,
            "--private-key",
            ssh_key_path,
            "-e",
            "ansible_ssh_extra_args='-o StrictHostKeyChecking=no'",
        ]
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        logger.debug(f"Ansible stdout: {result.stdout}")

        if result.returncode != 0:
            logger.error(f"Ansible stderr: {result.stderr}")
            raise HTTPException(
                status_code=500,
                detail=f"Ansible command failed: {result.stderr.strip()}",
            )

        # Parse and structure the output
        structured_output = []
        host_output = {}
        for line in result.stdout.strip().split("\n"):
            host_match = re.match(r"^(\S+)\s\|\sSUCCESS\s=>\s{", line)
            if host_match:
                if host_output:
                    structured_output.append(host_output)
                host_output = {
                    "host": host_match.group(1).strip(),
                    "details": {"raw_output": ""},
                }
            elif host_output:
                host_output["details"]["raw_output"] += line + "\n"

        if host_output:
            structured_output.append(host_output)

        for host in structured_output:
            try:
                host["details"] = json.loads(host["details"]["raw_output"].strip())
            except json.JSONDecodeError:
                pass

        return {
            "message": "Ansible ping test successful",
            "results": structured_output,
        }

    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error running Ansible test: {str(e)}"
        )
    finally:
        # Clean up temporary inventory file
        if os.path.exists(temp_inventory_path):
            os.remove(temp_inventory_path)


"""
Endpoint: /stacks/{stack_id}/indexes
Description: This endpoint allows you to get or update the indexes for a stack.
HTTP Methods: GET, POST
"""


# GET /stacks/{stack_id}/indexes
@app.get("/stacks/{stack_id}/indexes")
async def get_indexes_endpoint(stack_id: str):
    # Check if the stack exists in Redis
    if not redis_client.exists(f"stack:{stack_id}:metadata"):
        raise HTTPException(status_code=404, detail=f"Stack '{stack_id}' not found.")

    # Retrieve indexes for the stack
    indexes = redis_client.hgetall(f"stack:{stack_id}:indexes")

    # Convert values from JSON to dict
    parsed_indexes = {name: json.loads(details) for name, details in indexes.items()}

    return {"stack_id": stack_id, "indexes": parsed_indexes}


# POST /stacks/{stack_id}/indexes
@app.post("/stacks/{stack_id}/indexes")
async def add_index(
    stack_id: str,
    splunk_username: str = Body(..., embed=True),
    splunk_password: str = Body(..., embed=True),
    name: str = Body(..., embed=True),
    maxDataSizeMB: int = Body(None, embed=True),
    datatype: str = Body(None, embed=True),
    apply_cluster_bundle: bool = Body(True, embed=True),  # Optional, default true
    apply_shc_bundle: bool = Body(True, embed=True),  # Optional, default true
):
    # Validate inputs
    if datatype not in [None, "event", "metric"]:
        raise HTTPException(
            status_code=400, detail="Invalid datatype. Must be 'event' or 'metric'."
        )

    # Set defaults
    maxDataSizeMB = maxDataSizeMB or 500 * 1024  # 500 GB in MB
    datatype = datatype or "event"

    # Load and update indexes
    indexes = get_indexes(stack_id)
    if name in indexes:
        return {
            "message": "Index already exists.",
            "index": indexes[name],
        }
    indexes[name] = {"maxDataSizeMB": maxDataSizeMB, "datatype": datatype}

    # Retrieve stack details
    stack_details = load_stack_from_redis(stack_id)

    # Prepare Ansible variables
    stack_metadata = redis_client.hgetall(f"stack:{stack_id}:metadata")
    if not stack_metadata:
        raise HTTPException(status_code=404, detail="Stack metadata not found.")

    ansible_vars = {
        "index_name": name,
        "maxDataSizeMB": maxDataSizeMB,
        "datatype": datatype,
    }

    if stack_metadata["enterprise_deployment_type"] == "distributed":
        # Push to cluster manager
        ansible_vars["file_path"] = (
            "/opt/splunk/etc/manager-apps/001_splunk_aem/local/indexes.conf"
        )
        await run_ansible_playbook(
            stack_id=stack_id,
            playbook_name="add_index.yml",
            ansible_vars=ansible_vars,
            limit=stack_metadata["cluster_manager_node"],
            creds={"username": splunk_username, "password": splunk_password},
        )

        # Apply cluster bundle if enabled
        if apply_cluster_bundle:
            await run_ansible_playbook(
                stack_id=stack_id,
                playbook_name="apply_cluster_bundle.yml",
                limit=stack_metadata["cluster_manager_node"],
                creds={"username": splunk_username, "password": splunk_password},
            )

        # Push to SHC if enabled
        if stack_metadata.get("shc_cluster", "false").lower() == "true":
            ansible_vars["shc_deployer_node"] = stack_details["shc_deployer_node"]
            ansible_vars["shc_members"] = stack_details["shc_members"]
            ansible_vars["file_path"] = (
                "/opt/splunk/etc/shcluster/apps/001_splunk_aem/local/indexes.conf"
            )
            await run_ansible_playbook(
                stack_id=stack_id,
                playbook_name="add_index.yml",
                ansible_vars=ansible_vars,
                limit=stack_metadata["shc_deployer_node"],
                creds={"username": splunk_username, "password": splunk_password},
            )

            # Apply SHC bundle if enabled
            if apply_shc_bundle:
                await run_ansible_playbook(
                    stack_id=stack_id,
                    playbook_name="apply_shc_bundle.yml",
                    ansible_vars=ansible_vars,
                    limit=stack_metadata["shc_deployer_node"],
                    creds={"username": splunk_username, "password": splunk_password},
                )

    else:
        # Standalone deployment
        ansible_vars["file_path"] = (
            "/opt/splunk/etc/apps/001_splunk_aem/local/indexes.conf"
        )
        await run_ansible_playbook(
            stack_id=stack_id,
            playbook_name="add_index.yml",
            ansible_vars=ansible_vars,
            limit="all",
            creds={"username": splunk_username, "password": splunk_password},
        )

    # Save the updated indexes to Redis
    save_indexes(stack_id, indexes)

    return {
        "message": "Index added successfully, for distributed stacks, push the bundle to reflect this new configuration, on standalone stacks, ensure to restart Splunk.",
        "index": indexes[name],
    }


# POST /stacks/{stack_id}/batch_indexes
@app.post("/stacks/{stack_id}/batch_indexes")
async def batch_add_indexes(
    stack_id: str,
    splunk_username: str = Body(...),
    splunk_password: str = Body(...),
    indexes: List[Dict[str, Any]] = Body(...),
    apply_cluster_bundle: bool = Body(...),
    apply_shc_bundle: bool = Body(...),
):
    """
    Batch create multiple indexes in a single request.
    """
    stack_metadata = redis_client.hgetall(f"stack:{stack_id}:metadata")
    if not stack_metadata:
        raise HTTPException(status_code=404, detail="Stack metadata not found.")

    stack_details = load_stack_from_redis(stack_id)
    existing_indexes = get_indexes(stack_id)

    # Prepare the list of indexes for Ansible
    ansible_indexes = []
    for index in indexes:
        name = index.get("name")
        maxDataSizeMB = index.get("maxDataSizeMB", 500 * 1024)  # Default 500GB in MB
        datatype = index.get("datatype", "event")

        # Skip index if it already exists
        if name in existing_indexes:
            continue

        # Build structured index object
        ansible_indexes.append(
            {
                "name": name,
                "options": [
                    {"option": "maxDataSizeMB", "value": str(maxDataSizeMB)},
                    {"option": "datatype", "value": datatype},
                ],
            }
        )

    # No new indexes to add
    if not ansible_indexes:
        return {"message": "No new indexes to add."}

    # Run a single Ansible playbook call for all indexes
    ansible_vars = {
        "indexes": ansible_indexes,
        "file_path": "/opt/splunk/etc/apps/001_splunk_aem/local/indexes.conf",
    }

    if stack_metadata["enterprise_deployment_type"] == "distributed":
        ansible_vars["file_path"] = (
            "/opt/splunk/etc/manager-apps/001_splunk_aem/local/indexes.conf"
        )
        await run_ansible_playbook(
            stack_id=stack_id,
            playbook_name="batch_add_indexes.yml",
            ansible_vars=ansible_vars,
            limit=stack_metadata["cluster_manager_node"],
            creds={"username": splunk_username, "password": splunk_password},
        )

        if apply_cluster_bundle:
            await run_ansible_playbook(
                stack_id=stack_id,
                playbook_name="apply_cluster_bundle.yml",
                limit=stack_metadata["cluster_manager_node"],
                creds={"username": splunk_username, "password": splunk_password},
            )

        if stack_metadata.get("shc_cluster", "false").lower() == "true":
            ansible_vars["shc_deployer_node"] = stack_details["shc_deployer_node"]
            ansible_vars["shc_members"] = stack_details["shc_members"]
            ansible_vars["file_path"] = (
                "/opt/splunk/etc/shcluster/apps/001_splunk_aem/local/indexes.conf"
            )
            await run_ansible_playbook(
                stack_id=stack_id,
                playbook_name="batch_add_indexes.yml",
                ansible_vars=ansible_vars,
                limit=stack_metadata["shc_deployer_node"],
                creds={"username": splunk_username, "password": splunk_password},
            )

            if apply_shc_bundle:
                await run_ansible_playbook(
                    stack_id=stack_id,
                    playbook_name="apply_shc_bundle.yml",
                    ansible_vars=ansible_vars,
                    limit=stack_metadata["shc_deployer_node"],
                    creds={"username": splunk_username, "password": splunk_password},
                )

    else:
        await run_ansible_playbook(
            stack_id=stack_id,
            playbook_name="batch_add_indexes.yml",
            ansible_vars=ansible_vars,
            limit="all",
            creds={"username": splunk_username, "password": splunk_password},
        )

    # Save the updated indexes to Redis
    for index in ansible_indexes:
        existing_indexes[index["name"]] = {
            "maxDataSizeMB": next(
                item["value"]
                for item in index["options"]
                if item["option"] == "maxDataSizeMB"
            ),
            "datatype": next(
                item["value"]
                for item in index["options"]
                if item["option"] == "datatype"
            ),
        }

    save_indexes(stack_id, existing_indexes)

    return {
        "message": "Batch index creation complete.",
        "created_indexes": ansible_indexes,
    }


# DELETE /stacks/{stack_id}/indexes/{index_name}
@app.delete("/stacks/{stack_id}/indexes/{index_name}")
async def delete_index_endpoint(
    stack_id: str,
    index_name: str,
    splunk_username: str = Query(..., description="Splunk admin username"),
    splunk_password: str = Query(..., description="Splunk admin password"),
    apply_cluster_bundle: bool = Query(True, description="Apply cluster bundle"),
    apply_shc_bundle: bool = Query(True, description="Apply SHC bundle"),
):
    # Validate stack existence
    if not redis_client.exists(f"stack:{stack_id}:metadata"):
        raise HTTPException(status_code=404, detail=f"Stack '{stack_id}' not found.")

    # Load and validate the index
    indexes = redis_client.hgetall(f"stack:{stack_id}:indexes")
    if index_name not in indexes:
        raise HTTPException(status_code=404, detail=f"Index '{index_name}' not found.")

    # Retrieve stack details
    stack_details = load_stack_from_redis(stack_id)

    # Retrieve stack metadata
    stack_metadata = redis_client.hgetall(f"stack:{stack_id}:metadata")
    if not stack_metadata:
        raise HTTPException(
            status_code=404, detail=f"Metadata for stack '{stack_id}' not found."
        )

    # Create temporary inventory file
    inventory_data = redis_client.get(f"stack:{stack_id}:inventory")
    if not inventory_data:
        raise HTTPException(
            status_code=404, detail=f"Inventory not found for stack '{stack_id}'."
        )

    temp_inventory_path = f"/tmp/{stack_id}_inventory.ini"
    try:
        with open(temp_inventory_path, "w") as f:
            f.write(inventory_data)

        # Prepare Ansible variables
        ansible_vars = {
            "index_name": index_name,
        }
        enterprise_type = stack_metadata["enterprise_deployment_type"]

        if enterprise_type == "distributed":
            # Remove from cluster manager
            ansible_vars["file_path"] = (
                "/opt/splunk/etc/manager-apps/001_splunk_aem/local/indexes.conf"
            )
            await run_ansible_playbook(
                stack_id=stack_id,
                playbook_name="remove_index.yml",
                ansible_vars=ansible_vars,
                limit=stack_metadata["cluster_manager_node"],
                creds={"username": splunk_username, "password": splunk_password},
            )

            # Apply cluster bundle if requested
            if apply_cluster_bundle:
                await run_ansible_playbook(
                    stack_id=stack_id,
                    playbook_name="apply_cluster_bundle.yml",
                    limit=stack_metadata["cluster_manager_node"],
                    creds={"username": splunk_username, "password": splunk_password},
                )

            # Remove from SHC deployer if enabled
            if stack_metadata.get("shc_cluster") == "True":
                ansible_vars["shc_deployer_node"] = stack_details["shc_deployer_node"]
                ansible_vars["shc_members"] = stack_details["shc_members"]
                ansible_vars["file_path"] = (
                    "/opt/splunk/etc/shcluster/apps/001_splunk_aem/local/indexes.conf"
                )
                await run_ansible_playbook(
                    stack_id=stack_id,
                    playbook_name="remove_index.yml",
                    ansible_vars=ansible_vars,
                    limit=stack_metadata["shc_deployer_node"],
                    creds={"username": splunk_username, "password": splunk_password},
                )

                # Apply SHC bundle if requested
                if apply_shc_bundle:
                    await run_ansible_playbook(
                        stack_id=stack_id,
                        playbook_name="apply_shc_bundle.yml",
                        ansible_vars=ansible_vars,
                        limit=stack_metadata["shc_deployer_node"],
                        creds={
                            "username": splunk_username,
                            "password": splunk_password,
                        },
                    )

        elif enterprise_type == "standalone":
            # Remove for standalone deployments
            ansible_vars["file_path"] = (
                "/opt/splunk/etc/apps/001_splunk_aem/local/indexes.conf"
            )
            await run_ansible_playbook(
                stack_id=stack_id,
                playbook_name="remove_index.yml",
                ansible_vars=ansible_vars,
                limit="all",
                creds={"username": splunk_username, "password": splunk_password},
            )

        # Remove the index from Redis
        removed_index_data = indexes.pop(index_name, None)
        redis_client.hdel(f"stack:{stack_id}:indexes", index_name)

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error running playbook: {str(e)}")
    finally:
        # Clean up temporary file
        if os.path.exists(temp_inventory_path):
            os.remove(temp_inventory_path)

    return {
        "message": f"Index '{index_name}' deleted successfully. For this change to take effect, ensure to push the bundles for distributed environments or restart Splunk for standalone.",
        "index": index_name,
    }


"""
Endpoint: /stacks/{stack_id}/installed_apps
Description: This endpoint allows you to get the list of installed apps on a stack.
HTTP Method: GET
"""


# GET /stacks/{stack_id}/installed_apps
@app.get("/stacks/{stack_id}/installed_apps", summary="Retrieve installed Splunk apps")
async def get_installed_apps(stack_id: str):
    """
    Retrieve the list of installed Splunk apps for a given stack using Redis.
    """
    try:
        # Ensure the stack exists
        stack_details = load_stack_from_redis(stack_id)

        # Retrieve installed apps from Redis
        installed_apps = redis_client.hgetall(f"stack:{stack_id}:apps")

        # Deserialize app details from JSON
        installed_apps = {
            app_name: json.loads(app_details)
            for app_name, app_details in installed_apps.items()
        }

        # Log installed apps
        logger.debug(
            f"Installed apps on stack {stack_id}: {json.dumps(installed_apps, indent=4)}"
        )

        return {"stack_id": stack_id, "installed_apps": installed_apps}

    except Exception as e:
        logger.error(
            f"Error retrieving installed apps for stack '{stack_id}': {str(e)}"
        )
        raise HTTPException(
            status_code=500,
            detail=f"Unable to retrieve installed apps for stack '{stack_id}'.",
        )


"""
Endpoint: /stacks/{stack_id}/install_splunk_app
Description: This endpoint allows you to install a Splunk app on a stack.
HTTP Method: POST
"""


# POST /stacks/{stack_id}/install_splunk_app
@app.post("/stacks/{stack_id}/install_splunk_app", summary="Install a Splunk app")
async def install_splunk_app(
    stack_id: str,
    splunk_username: str = Body(..., embed=True),
    splunk_password: str = Body(..., embed=True),
    splunkbase_username: str = Body(..., embed=True),
    splunkbase_password: str = Body(..., embed=True),
    splunkbase_app_id: str = Body(..., embed=True),
    splunkbase_app_name: str = Body(..., embed=True),
    version: str = Body(..., embed=True),
    apply_shc_bundle: bool = Body(True, embed=True),  # Optional parameter
):
    try:
        # Retrieve stack details
        stack_details = load_stack_from_redis(stack_id)

        # Retrieve existing apps from Redis
        installed_apps = redis_client.hgetall(f"stack:{stack_id}:apps")
        installed_apps = {
            app_name: json.loads(app_details)
            for app_name, app_details in installed_apps.items()
        }

        # Check if the app is already installed with the requested version
        if (
            splunkbase_app_name in installed_apps
            and installed_apps[splunkbase_app_name]["version"] == version
        ):
            return {
                "message": f"App '{splunkbase_app_name}' is already installed with version {version}.",
                "app_details": installed_apps[splunkbase_app_name],
            }

        logger.debug(
            f"App '{splunkbase_app_name}' is not installed with version {version}. Downloading and installing."
        )

        # Path to the downloaded tarball
        files_dir = "/app/data/splunk_apps"
        os.makedirs(files_dir, exist_ok=True)
        app_tar_path = os.path.join(files_dir, f"{splunkbase_app_name}_{version}.tgz")

        # Log in to Splunk Base
        session_id = login_splunkbase(
            splunkbase_username, splunkbase_password, proxy_dict={}
        )

        # Download app tarball if not already downloaded
        if not os.path.exists(app_tar_path):
            app_download_url = f"https://splunkbase.splunk.com/app/{splunkbase_app_id}/release/{version}/download/"
            response = requests.get(
                app_download_url,
                headers={"X-Auth-Token": session_id},
                stream=True,
                allow_redirects=True,
            )

            if response.status_code != 200:
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to download app from Splunk Base: {response.text}",
                )

            with open(app_tar_path, "wb") as f:
                f.write(response.content)
            logger.info(f"App downloaded successfully: {app_tar_path}")

        # Ensure Ansible's files directory exists and copy tarball
        ansible_files_dir = "/app/ansible/files"
        os.makedirs(ansible_files_dir, exist_ok=True)
        ansible_tar_path = os.path.join(ansible_files_dir, f"{splunkbase_app_name}.tgz")
        shutil.copy(app_tar_path, ansible_tar_path)

        # Determine the appropriate Ansible playbook
        playbook = (
            "install_standalone_app.yml"
            if stack_details["enterprise_deployment_type"] == "standalone"
            else "install_shc_app.yml"
        )

        # Prepare Ansible variables
        ansible_vars = {"splunk_app_name": splunkbase_app_name}
        if stack_details["enterprise_deployment_type"] != "standalone":
            ansible_vars.update(
                {"shc_deployer_node": stack_details["shc_deployer_node"]}
            )

        # Run the Ansible playbook
        await run_ansible_playbook(
            stack_id,
            playbook,
            ansible_vars=ansible_vars,
            creds={"username": splunk_username, "password": splunk_password},
        )

        # Apply SHC bundle if needed and requested
        if stack_details["enterprise_deployment_type"] != "standalone":
            if stack_details.get("shc_cluster") and apply_shc_bundle:
                ansible_vars = {
                    "shc_deployer_node": stack_details["shc_deployer_node"],
                    "shc_members": stack_details["shc_members"],
                }
                await run_ansible_playbook(
                    stack_id,
                    "apply_shc_bundle.yml",
                    ansible_vars=ansible_vars,
                    limit=stack_details["shc_deployer_node"],
                    creds={"username": splunk_username, "password": splunk_password},
                )

        # Update Redis with the new app details
        redis_client.hset(
            f"stack:{stack_id}:apps",
            splunkbase_app_name,
            json.dumps({"id": splunkbase_app_id, "version": version}),
        )

        return {
            "message": "App installed successfully",
            "app_details": {"id": splunkbase_app_id, "version": version},
        }

    except Exception as e:
        error_message = f'Error installing app "{splunkbase_app_name}": {str(e)}'
        logger.error(error_message)
        raise HTTPException(status_code=500, detail=error_message)


# POST /stacks/{stack_id}/batch_install_apps
@app.post("/stacks/{stack_id}/batch_install_apps", summary="Batch install Splunk apps")
async def batch_install_apps(
    stack_id: str,
    splunk_username: str = Body(...),
    splunk_password: str = Body(...),
    splunkbase_username: str = Body(...),
    splunkbase_password: str = Body(...),
    apply_shc_bundle: bool = Body(...),  # Optional parameter
    apps: List[Dict[str, Any]] = Body(...),  # List of apps to install
):
    try:
        stack_details = load_stack_from_redis(stack_id)
        installed_apps = redis_client.hgetall(f"stack:{stack_id}:apps")
        installed_apps = {
            app_name: json.loads(app_details)
            for app_name, app_details in installed_apps.items()
        }

        session_id = login_splunkbase(
            splunkbase_username, splunkbase_password, proxy_dict={}
        )
        files_dir = "/app/data/splunk_apps"
        os.makedirs(files_dir, exist_ok=True)

        results = []

        for app in apps:
            app_id = app["splunkbase_app_id"]
            app_name = app["splunkbase_app_name"]
            version = app["version"]

            if (
                app_name in installed_apps
                and installed_apps[app_name]["version"] == version
            ):
                results.append(
                    {
                        "app_name": app_name,
                        "message": f"Already installed (version {version})",
                    }
                )
                continue

            app_tar_path = os.path.join(files_dir, f"{app_name}_{version}.tgz")

            if not os.path.exists(app_tar_path):
                download_splunk_app(session_id, app_id, version, app_tar_path)

            ansible_files_dir = "/app/ansible/files"
            os.makedirs(ansible_files_dir, exist_ok=True)
            ansible_tar_path = os.path.join(ansible_files_dir, f"{app_name}.tgz")
            shutil.copy(app_tar_path, ansible_tar_path)

            playbook = (
                "install_standalone_app.yml"
                if stack_details["enterprise_deployment_type"] == "standalone"
                else "install_shc_app.yml"
            )

            ansible_vars = {"splunk_app_name": app_name}
            if stack_details["enterprise_deployment_type"] != "standalone":
                ansible_vars.update(
                    {"shc_deployer_node": stack_details["shc_deployer_node"]}
                )

            await run_ansible_playbook(
                stack_id,
                playbook,
                ansible_vars=ansible_vars,
                creds={"username": splunk_username, "password": splunk_password},
            )

            redis_client.hset(
                f"stack:{stack_id}:apps",
                app_name,
                json.dumps({"id": app_id, "version": version}),
            )

            results.append({"app_name": app_name, "message": "Installed successfully"})

        if (
            stack_details["enterprise_deployment_type"] != "standalone"
            and apply_shc_bundle
        ):
            ansible_vars = {
                "shc_deployer_node": stack_details["shc_deployer_node"],
                "shc_members": stack_details["shc_members"],
            }
            await run_ansible_playbook(
                stack_id,
                "apply_shc_bundle.yml",
                ansible_vars=ansible_vars,
                limit=stack_details["shc_deployer_node"],
                creds={"username": splunk_username, "password": splunk_password},
            )

        return {"message": "Batch app installation completed", "results": results}

    except Exception as e:
        logger.error(f"Batch install error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Batch install failed: {str(e)}")


"""
Endpoint: /stacks/{stack_id}/delete_splunk_app
Description: This endpoint allows you to delete a Splunk app from a stack.
HTTP Method: DELETE
"""


# DELETE /stacks/{stack_id}/delete_splunk_app
@app.delete("/stacks/{stack_id}/delete_splunk_app", summary="Delete a Splunk app")
async def delete_splunk_app(
    stack_id: str,
    splunkbase_app_name: str = Query(
        ..., description="Name of the Splunk app to delete"
    ),
    splunk_username: str = Query(..., description="Splunk admin username"),
    splunk_password: str = Query(..., description="Splunk admin password"),
    apply_shc_bundle: bool = Query(
        True, description="Whether to apply the SHC bundle after deletion"
    ),  # Optional parameter
):
    try:
        # Retrieve stack details
        stack_details = load_stack_from_redis(stack_id)

        # Retrieve existing apps from Redis
        installed_apps = redis_client.hgetall(f"stack:{stack_id}:apps")
        installed_apps = {
            app_name: json.loads(app_details)
            for app_name, app_details in installed_apps.items()
        }

        # Check if the app exists
        if splunkbase_app_name not in installed_apps:
            raise HTTPException(
                status_code=404,
                detail=f"App '{splunkbase_app_name}' not found in this stack's installed apps.",
            )

        logger.debug(f"Deleting app '{splunkbase_app_name}' from stack '{stack_id}'.")

        # Determine the appropriate Ansible playbook
        playbook = (
            "remove_standalone_app.yml"
            if stack_details["enterprise_deployment_type"] == "standalone"
            else "remove_shc_app.yml"
        )

        # Prepare Ansible variables
        ansible_vars = {"splunk_app_name": splunkbase_app_name}
        if stack_details["enterprise_deployment_type"] != "standalone":
            ansible_vars.update(
                {"shc_deployer_node": stack_details["shc_deployer_node"]}
            )

        # Run the Ansible playbook to remove the app
        await run_ansible_playbook(
            stack_id,
            playbook,
            ansible_vars=ansible_vars,
            creds={"username": splunk_username, "password": splunk_password},
        )

        # If SHC and apply_shc_bundle is true, apply the SHC bundle
        if stack_details["enterprise_deployment_type"] != "standalone":
            if stack_details.get("shc_cluster") and apply_shc_bundle:
                ansible_vars = {
                    "shc_deployer_node": stack_details["shc_deployer_node"],
                    "shc_members": stack_details["shc_members"],
                }
                await run_ansible_playbook(
                    stack_id,
                    "apply_shc_bundle.yml",
                    ansible_vars=ansible_vars,
                    limit=stack_details["shc_deployer_node"],
                    creds={"username": splunk_username, "password": splunk_password},
                )

        # Remove the app from Redis
        redis_client.hdel(f"stack:{stack_id}:apps", splunkbase_app_name)

        logger.info(
            f"App '{splunkbase_app_name}' successfully deleted from stack '{stack_id}'."
        )

        return {
            "message": f"App '{splunkbase_app_name}' deleted successfully.",
            "remaining_apps": {
                app_name: json.loads(details)
                for app_name, details in redis_client.hgetall(
                    f"stack:{stack_id}:apps"
                ).items()
            },
        }

    except Exception as e:
        error_message = f'Error deleting app "{splunkbase_app_name}": {str(e)}'
        logger.error(error_message)
        raise HTTPException(
            status_code=500,
            detail=error_message,
        )


"""
Endpoint: /stacks/{stack_id}/install_private_app
Description: This endpoint allows you to install a private Splunk app on a stack.
HTTP Method: POST
"""


# POST /stacks/{stack_id}/install_private_app
@app.post(
    "/stacks/{stack_id}/install_private_app", summary="Install a private Splunk app"
)
async def install_private_app(
    stack_id: str,
    app_base64: str = Body(..., embed=True),  # Base64-encoded tarball
    app_name: str = Body(..., embed=True),  # Name of the private app
    splunk_username: str = Body(..., embed=True),
    splunk_password: str = Body(..., embed=True),
    target: Optional[str] = Body(None, embed=True),  # Target for distributed stacks
    apply_shc_bundle: bool = Body(True, embed=True),  # Optional SHC bundle application
):
    try:
        # Retrieve stack details
        stack_details = load_stack_from_redis(stack_id)
        enterprise_type = stack_details["enterprise_deployment_type"]

        # Decode and save the tarball
        files_dir = "/app/data/splunk_private_apps"
        os.makedirs(files_dir, exist_ok=True)
        app_tar_path = os.path.join(files_dir, f"{app_name}.tgz")
        with open(app_tar_path, "wb") as f:
            f.write(base64.b64decode(app_base64))

        # Copy tarball to Ansible's files directory
        ansible_files_dir = "/app/ansible/files"
        os.makedirs(ansible_files_dir, exist_ok=True)
        ansible_tar_path = os.path.join(ansible_files_dir, f"{app_name}.tgz")
        shutil.copy(app_tar_path, ansible_tar_path)

        # Select appropriate playbook and target
        playbook = "install_standalone_app.yml"
        ansible_vars = {"splunk_app_name": app_name}
        limit = None

        if enterprise_type == "distributed":
            if target == "shc":
                playbook = "install_shc_app.yml"
                ansible_vars.update(
                    {
                        "shc_deployer_node": stack_details["shc_deployer_node"],
                    }
                )
                limit = stack_details["shc_deployer_node"]
            else:
                playbook = "install_standalone_app.yml"
                limit = target
        else:
            limit = "all"

        # Run Ansible playbook
        await run_ansible_playbook(
            stack_id,
            playbook,
            ansible_vars=ansible_vars,
            limit=limit,
            creds={"username": splunk_username, "password": splunk_password},
        )

        # Apply SHC bundle if requested
        if target == "shc" and apply_shc_bundle:
            await run_ansible_playbook(
                stack_id,
                "apply_shc_bundle.yml",
                ansible_vars={
                    "shc_deployer_node": stack_details["shc_deployer_node"],
                    "shc_members": stack_details["shc_members"],
                },
                limit=stack_details["shc_deployer_node"],
                creds={"username": splunk_username, "password": splunk_password},
            )

        return {
            "message": f"Private app '{app_name}' installed successfully.",
        }

    except Exception as e:
        error_message = f'Error installing private app "{app_name}": {str(e)}'
        logger.error(error_message)
        raise HTTPException(status_code=500, detail=error_message)


"""
Endpoint: /stacks/{stack_id}/delete_private_app
Description: This endpoint allows you to remove a private Splunk app from a stack.
HTTP Method: DELETE
"""


# DELETE /stacks/{stack_id}/delete_private_app
@app.delete(
    "/stacks/{stack_id}/delete_private_app", summary="Remove a private Splunk app"
)
async def remove_private_app(
    stack_id: str,
    app_name: str = Query(...),  # Name of the private app
    splunk_username: str = Query(...),
    splunk_password: str = Query(...),
    target: Optional[str] = Query(None),  # Target for distributed stacks
    apply_shc_bundle: bool = Query(True),  # Optional SHC bundle application
):
    try:
        # Retrieve stack details
        stack_details = load_stack_from_redis(stack_id)
        enterprise_type = stack_details["enterprise_deployment_type"]

        # Select appropriate playbook and target
        playbook = "remove_standalone_app.yml"
        ansible_vars = {"splunk_app_name": app_name}
        limit = None

        if enterprise_type == "distributed":
            if target == "shc":
                playbook = "remove_shc_app.yml"
                ansible_vars.update(
                    {
                        "shc_deployer_node": stack_details["shc_deployer_node"],
                    }
                )
                limit = stack_details["shc_deployer_node"]
            else:
                playbook = "remove_standalone_app.yml"
                limit = target
        else:
            limit = "all"

        # Run Ansible playbook
        await run_ansible_playbook(
            stack_id,
            playbook,
            ansible_vars=ansible_vars,
            limit=limit,
            creds={"username": splunk_username, "password": splunk_password},
        )

        # Apply SHC bundle if requested
        if target == "shc" and apply_shc_bundle:
            await run_ansible_playbook(
                stack_id,
                "apply_shc_bundle.yml",
                ansible_vars={
                    "shc_deployer_node": stack_details["shc_deployer_node"],
                    "shc_members": stack_details["shc_members"],
                },
                limit=stack_details["shc_deployer_node"],
                creds={"username": splunk_username, "password": splunk_password},
            )

        return {
            "message": f"Private app '{app_name}' removed successfully.",
        }

    except Exception as e:
        error_message = f'Error removing private app "{app_name}": {str(e)}'
        logger.error(error_message)
        raise HTTPException(status_code=500, detail=error_message)


"""
Endpoint: /stacks/{stack_id}/shc_rolling_restart
Description: This endpoint allows you to trigger a rolling restart of an SHC cluster.
HTTP Method: POST
"""


# POST /stacks/{stack_id}/shc_rolling_restart
@app.post(
    "/stacks/{stack_id}/shc_rolling_restart",
    summary="Trigger SHC rolling restart for a stack",
)
async def shc_rolling_restart(
    stack_id: str,
    splunk_username: str = Body(..., embed=True),
    splunk_password: str = Body(..., embed=True),
):
    try:
        # Retrieve stack details from Redis
        stack_details = load_stack_from_redis(stack_id)

        # Validate the deployment type
        if stack_details["enterprise_deployment_type"] == "standalone":
            raise HTTPException(
                status_code=400,
                detail="SHC cluster is not enabled for this stack.",
            )

        # Prepare Ansible variables
        ansible_vars = {
            "shc_deployer_node": stack_details["shc_deployer_node"],
            "shc_members": stack_details["shc_members"],
        }

        # Trigger Rolling Restart via Ansible playbook
        await run_ansible_playbook(
            stack_id,
            "trigger_shc_rolling_restart.yml",
            ansible_vars=ansible_vars,
            limit=stack_details["shc_deployer_node"],
            creds={"username": splunk_username, "password": splunk_password},
        )

        logger.info(f"SHC rolling restart triggered for stack '{stack_id}'.")

        return {
            "message": "SHC Rolling Restart triggered successfully",
        }

    except HTTPException as e:
        logger.error(f"Error in SHC rolling restart for stack '{stack_id}': {e.detail}")
        raise
    except Exception as e:
        logger.error(f"Unexpected error during SHC rolling restart: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Unable to trigger SHC rolling restart: {str(e)}",
        )


"""
Endpoint: /stacks/{stack_id}/cluster_rolling_restart
Description: This endpoint allows you to trigger a rolling restart of an indexer cluster.
HTTP Method: POST
"""


# POST /stacks/{stack_id}/cluster_rolling_restart
@app.post("/stacks/{stack_id}/cluster_rolling_restart")
async def cluster_rolling_restart(
    stack_id: str,
    splunk_username: str = Body(..., embed=True),
    splunk_password: str = Body(..., embed=True),
):
    # Retrieve stack metadata from Redis
    stack_metadata = redis_client.hgetall(f"stack:{stack_id}:metadata")
    if not stack_metadata:
        raise HTTPException(
            status_code=404, detail=f"Metadata for stack '{stack_id}' not found."
        )

    # Validate the deployment type
    if stack_metadata["enterprise_deployment_type"] == "standalone":
        raise HTTPException(
            status_code=400, detail="Indexer cluster is not enabled for this stack."
        )

    # Validate that cluster_manager_node is specified
    cluster_manager_node = stack_metadata.get("cluster_manager_node")
    if not cluster_manager_node:
        raise HTTPException(
            status_code=400, detail="Cluster Manager Node is required for this action."
        )

    # Prepare Ansible variables
    ansible_vars = {
        "target_node": cluster_manager_node,
    }

    # Trigger the rolling restart using Ansible
    try:
        await run_ansible_playbook(
            stack_id=stack_id,
            playbook_name="trigger_cluster_rolling_restart.yml",
            ansible_vars=ansible_vars,
            limit=cluster_manager_node,
            creds={"username": splunk_username, "password": splunk_password},
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to trigger rolling restart: {str(e)}",
        )

    return {
        "message": "Indexer Cluster Rolling Restart triggered successfully.",
    }


"""
Endpoint: /stacks/{stack_id}/restart_splunk
Description: This endpoint allows you to restart Splunk services on a stack.
HTTP Method: POST
"""


# POST /stacks/{stack_id}/restart_splunk
@app.post("/stacks/{stack_id}/restart_splunk")
async def restart_splunk(
    stack_id: str,
    limit: Optional[str] = Body(None, embed=True),  # Optional limit parameter
    splunk_service_name: Optional[str] = Body(
        "splunk", embed=True
    ),  # Default to splunk
):

    # Retrieve stack details from Redis
    stack_details = load_stack_from_redis(stack_id)

    # Retrieve stack metadata from Redis
    stack_metadata = redis_client.hgetall(f"stack:{stack_id}:metadata")
    if not stack_metadata:
        raise HTTPException(
            status_code=404, detail=f"Metadata for stack '{stack_id}' not found."
        )

    # Prepare Ansible variables
    ansible_vars = {
        "splunk_service_name": splunk_service_name,
    }

    # Parse and validate the limit parameter
    limit_hosts = None
    if limit:
        limit_hosts = ",".join([host.strip() for host in limit.split(",")])

    # If the deployment is distributed, limit is mandatory
    if stack_metadata["enterprise_deployment_type"] != "standalone" and not limit_hosts:
        raise HTTPException(
            status_code=400,
            detail="Limit parameter is required for distributed deployments.",
        )

    # Execute the Ansible playbook
    try:
        await run_ansible_playbook(
            stack_id=stack_id,
            playbook_name="restart_splunk.yml",
            ansible_vars=ansible_vars,
            limit=limit_hosts,
            creds=None,
        )
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to trigger Splunk restart: {str(e)}"
        )

    if stack_details["enterprise_deployment_type"] == "standalone":
        return {
            "message": f"Splunk Restart triggered successfully.",
        }

    else:
        return {
            "message": f"Splunk Restart triggered successfully for hosts: {limit_hosts}",
        }


"""
Endpoint: /stacks/{stack_id}/apply_cluster_bundle
Description: This endpoint applies a cluster bundle on a distributed cluster manager.
HTTP Method: POST
"""


@app.post("/stacks/{stack_id}/apply_cluster_bundle", summary="Apply cluster bundle")
async def apply_cluster_bundle(
    stack_id: str,
    splunk_username: str = Body(..., embed=True),
    splunk_password: str = Body(..., embed=True),
):
    """
    Apply cluster bundle on a distributed cluster manager.
    """
    try:
        # Retrieve stack metadata from Redis
        stack_metadata = redis_client.hgetall(f"stack:{stack_id}:metadata")
        if not stack_metadata:
            raise HTTPException(
                status_code=404, detail=f"Metadata for stack '{stack_id}' not found."
            )

        # Validate deployment type
        if stack_metadata["enterprise_deployment_type"] != "distributed":
            raise HTTPException(
                status_code=400,
                detail="Cluster bundle application is only valid for distributed deployments.",
            )

        # Validate cluster manager node
        cluster_manager_node = stack_metadata.get("cluster_manager_node")
        if not cluster_manager_node:
            raise HTTPException(
                status_code=400,
                detail="Cluster manager node is required for this operation.",
            )

        # Run the Ansible playbook to apply the cluster bundle
        await run_ansible_playbook(
            stack_id=stack_id,
            playbook_name="apply_cluster_bundle.yml",
            ansible_vars={},
            limit=cluster_manager_node,
            creds={"username": splunk_username, "password": splunk_password},
        )

        return {"message": "Cluster bundle applied successfully."}

    except Exception as e:
        logger.error(f"Error applying cluster bundle for stack '{stack_id}': {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Error applying cluster bundle: {str(e)}"
        )


"""
Endpoint: /stacks/{stack_id}/apply_shc_bundle
Description: This endpoint applies an SHC bundle on a deployer node.
HTTP Method: POST
"""


@app.post("/stacks/{stack_id}/apply_shc_bundle", summary="Apply SHC bundle")
async def apply_shc_bundle(
    stack_id: str,
    splunk_username: str = Body(..., embed=True),
    splunk_password: str = Body(..., embed=True),
):
    """
    Apply SHC bundle on a deployer node.
    """
    try:
        # Retrieve stack metadata from Redis
        stack_metadata = redis_client.hgetall(f"stack:{stack_id}:metadata")
        if not stack_metadata:
            raise HTTPException(
                status_code=404, detail=f"Metadata for stack '{stack_id}' not found."
            )

        # Validate SHC cluster configuration
        if not stack_metadata.get("shc_cluster", "").lower() == "true":
            raise HTTPException(
                status_code=400,
                detail="SHC bundle application is only valid for SHC clusters.",
            )

        # Validate SHC deployer node
        shc_deployer_node = stack_metadata.get("shc_deployer_node")
        if not shc_deployer_node:
            raise HTTPException(
                status_code=400,
                detail="SHC deployer node is required for this operation.",
            )

        # Prepare Ansible variables
        ansible_vars = {
            "shc_deployer_node": shc_deployer_node,
            "shc_members": stack_metadata.get("shc_members", "").split(","),
        }

        # Run the Ansible playbook to apply the SHC bundle
        await run_ansible_playbook(
            stack_id=stack_id,
            playbook_name="apply_shc_bundle.yml",
            ansible_vars=ansible_vars,
            limit=shc_deployer_node,
            creds={"username": splunk_username, "password": splunk_password},
        )

        return {"message": "SHC bundle applied successfully."}

    except Exception as e:
        logger.error(f"Error applying SHC bundle for stack '{stack_id}': {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Error applying SHC bundle: {str(e)}"
        )


@app.post("/stacks/{stack_id}/shc_set_http_max_content")
async def shc_set_http_max_content(
    stack_id: str,
    splunk_username: str = Body(..., embed=True),
    splunk_password: str = Body(..., embed=True),
    http_max_content_length: int = Body(5000000000, embed=True),  # Default to 5GB
):
    """
    Set HTTP Max Content Length for SHC members in server.conf.
    """
    try:
        # Load stack details
        stack_details = load_stack_from_redis(stack_id)
        stack_metadata = redis_client.hgetall(f"stack:{stack_id}:metadata")

        # Validate stack type and SHC configuration
        if stack_metadata[
            "enterprise_deployment_type"
        ] != "distributed" or not stack_details.get("shc_members"):
            raise HTTPException(
                status_code=400,
                detail="This action is only valid for distributed stacks with SHC enabled.",
            )

        # Prepare Ansible variables
        ansible_vars = {
            "http_max_content_length": http_max_content_length,
            "file_path": "/opt/splunk/etc/system/local/server.conf",
        }

        # Run the playbook limited to SHC members
        await run_ansible_playbook(
            stack_id=stack_id,
            playbook_name="shc_members_set_http_max_content.yml",
            ansible_vars=ansible_vars,
            limit=",".join(stack_details["shc_members"]),
            creds={"username": splunk_username, "password": splunk_password},
        )

        return {
            "message": "HTTP Max Content Length set successfully in server.conf. Please achieve a rolling restart now."
        }

    except Exception as e:
        logger.error(f"Error in setting HTTP Max Content Length: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Failed to set HTTP Max Content Length: {str(e)}"
        )
