from fastapi import FastAPI, HTTPException, Body
from pydantic import BaseModel
from typing import Dict, Optional, List
import logging
import json
import os
import subprocess
import base64
import shutil
import re
import requests
import xml.etree.ElementTree as ET

# Set up logging
logging.basicConfig(
    level=logging.DEBUG,  # Set to DEBUG for detailed logs
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

logger = logging.getLogger("main")

app = FastAPI()

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
    shc_members: Optional[List[str]] = None  # Optional unless SHC cluster
    ansible_python_interpreter: str = "/usr/bin/python3"  # Default Python interpreter


# Helper functions for the main file
def load_main_file():
    with open(MAIN_FILE, "r") as f:
        return json.load(f)


def save_main_file(data):
    with open(MAIN_FILE, "w") as f:
        json.dump(data, f, indent=4)


# Helper functions for individual stack files
def load_stack_file(stack_id):
    file_path = os.path.join(DATA_DIR, f"{stack_id}.json")
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Stack details not found.")
    with open(file_path, "r") as f:
        return json.load(f)


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


def get_indexes_file(stack_id: str):
    stack_dir = ensure_stack_dir(stack_id)
    indexes_file = os.path.join(stack_dir, "stack_indexes.json")
    if not os.path.exists(indexes_file):
        with open(indexes_file, "w") as f:
            json.dump({}, f)
    return indexes_file


def load_indexes(stack_id: str):
    indexes_file = get_indexes_file(stack_id)
    with open(indexes_file, "r") as f:
        return json.load(f)


def save_indexes(stack_id: str, data: dict):
    indexes_file = get_indexes_file(stack_id)
    with open(indexes_file, "w") as f:
        json.dump(data, f, indent=4)


def ensure_stack_dir(stack_id: str):
    stack_dir = os.path.join(DATA_DIR, stack_id)
    os.makedirs(stack_dir, exist_ok=True)
    return stack_dir


def run_ansible_playbook(
    stack_id: str,
    playbook_name: str,
    inventory_path: str,
    ansible_vars: dict = None,
    limit: str = None,
    creds: dict = None,
):
    stack_dir, inventory_path, ssh_key_path = get_stack_paths(stack_id)

    if not os.path.exists(stack_dir):
        raise HTTPException(status_code=404, detail=f"Stack '{stack_id}' not found.")
    if not os.path.exists(inventory_path):
        raise HTTPException(
            status_code=400, detail=f"Inventory file not found for stack '{stack_id}'."
        )
    if not os.path.exists(ssh_key_path):
        raise HTTPException(
            status_code=400, detail=f"SSH key not found for stack '{stack_id}'."
        )

    if creds:
        if ansible_vars is None:
            ansible_vars = {}
        ansible_vars["splunk_username"] = creds["username"]
        ansible_vars["splunk_password"] = creds["password"]

    playbook_dir = "/app/ansible"
    command = [
        "ansible-playbook",
        f"{playbook_dir}/{playbook_name}",
        "-i",
        inventory_path,
        "-e",
        json.dumps(ansible_vars),
        "-e",
        "ansible_ssh_extra_args='-o StrictHostKeyChecking=no'",
        "--private-key",
        ssh_key_path,
    ]

    if limit:
        command.extend(["--limit", limit])

    # Retrieve the stack details to get the Python interpreter
    if stack_id:
        stack_details = load_stack_file(stack_id)
        ansible_python_interpreter = stack_details.get(
            "ansible_python_interpreter", "/usr/bin/python3"
        )
        # Include the Python interpreter variable
        command.extend(
            ["-e", f"ansible_python_interpreter={ansible_python_interpreter}"]
        )

    # Sanitize the command for logging
    sanitized_command = command[:]
    if "splunk_password" in json.dumps(ansible_vars):
        sanitized_ansible_vars = ansible_vars.copy()
        sanitized_ansible_vars["splunk_password"] = "*****"
        sanitized_command[sanitized_command.index("-e") + 1] = json.dumps(
            sanitized_ansible_vars
        )

    logger.debug(f"Running Ansible playbook: {sanitized_command}")

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
            status_code=500, detail=f"Ansible playbook failed: {result.stderr.strip()}"
        )


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

    try:
        response = requests.get(
            download_url, headers=headers, stream=True, allow_redirects=True
        )

        if response.status_code != 200:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to download app. HTTP {response.status_code}: {response.text}",
            )

        # Write the response content to the file
        with open(output_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:  # Filter out keep-alive new chunks
                    f.write(chunk)

        logger.info(f"App downloaded successfully: {output_path}")
        return output_path

    except requests.exceptions.RequestException as e:
        logger.error(f"Error downloading Splunk app: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to download app: {str(e)}")


# Add logging middleware to capture API requests
@app.middleware("http")
async def log_requests(request, call_next):
    logger.info(f"Request: {request.method} {request.url}")
    response = await call_next(request)
    logger.info(f"Response: {response.status_code}")
    return response


# GET /stacks
@app.get("/stacks")
def get_all_stacks():
    main_data = load_main_file()
    return {"stacks": main_data.get("stacks", [])}


# POST /stacks
@app.post("/stacks")
def create_stack(stack: Stack):
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

    # Save stack data
    main_data = load_main_file()
    if stack.stack_id in [s["stack_id"] for s in main_data["stacks"]]:
        raise HTTPException(status_code=400, detail="Stack ID already exists.")

    main_data["stacks"].append(stack.dict())
    save_main_file(main_data)

    save_stack_file(stack.stack_id, stack.dict())
    return {"message": "Stack created successfully", "stack": stack.dict()}


# GET /stacks/{stack_id}
@app.get("/stacks/{stack_id}")
def get_stack(stack_id: str):
    return load_stack_file(stack_id)


# DELETE /stacks/{stack_id}
@app.delete("/stacks/{stack_id}")
def delete_stack(stack_id: str):
    main_data = load_main_file()
    stack_list = [s for s in main_data["stacks"] if s["stack_id"] != stack_id]

    if len(stack_list) == len(main_data["stacks"]):
        raise HTTPException(status_code=404, detail="Stack not found.")

    # Update main file
    main_data["stacks"] = stack_list
    save_main_file(main_data)

    # Remove individual stack file
    file_path = os.path.join(DATA_DIR, f"{stack_id}.json")
    if os.path.exists(file_path):
        os.remove(file_path)

    # Remove the entire stack directory
    stack_dir = os.path.join(DATA_DIR, stack_id)
    if os.path.exists(stack_dir):
        try:
            shutil.rmtree(stack_dir)  # Delete the directory and all its contents
        except Exception as e:
            logger.error(f"Failed to delete directory {stack_dir}: {e}")
            raise HTTPException(
                status_code=500,
                detail=f"Failed to delete stack directory: {e}",
            )

    return {"message": "Stack deleted successfully"}


# GET /stacks/{stack_id}/inventory
@app.get("/stacks/{stack_id}/inventory")
async def get_inventory(stack_id: str):
    stack_dir = ensure_stack_dir(stack_id)
    inventory_path = os.path.join(stack_dir, "inventory.ini")

    if not os.path.exists(inventory_path):
        raise HTTPException(status_code=404, detail="Inventory file not found.")

    try:
        with open(inventory_path, "r") as f:
            inventory_data = f.read()
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error reading inventory: {str(e)}"
        )

    return {"stack_id": stack_id, "inventory": inventory_data}


# POST /stacks/{stack_id}/inventory
@app.post("/stacks/{stack_id}/inventory")
async def upload_inventory(stack_id: str, inventory: Dict):
    # Ensure the stack exists
    stack_dir = ensure_stack_dir(stack_id)

    # Convert inventory JSON to Ansible INI format
    inventory_path = os.path.join(stack_dir, "inventory.ini")
    try:
        with open(inventory_path, "w") as f:
            for group, group_data in inventory.items():
                f.write(f"[{group}]\n")
                for host, vars_dict in group_data.get("hosts", {}).items():
                    vars_line = " ".join(
                        f"{key}={value}" for key, value in vars_dict.items()
                    )
                    f.write(f"{host} {vars_line}\n")
                f.write("\n")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error saving inventory: {str(e)}")

    return {
        "message": f"Inventory for stack '{stack_id}' saved successfully",
        "path": inventory_path,
    }


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


@app.post("/stacks/{stack_id}/ansible_test")
async def ansible_test(stack_id: str):
    stack_dir, inventory_path, ssh_key_path = get_stack_paths(stack_id)

    # Validate stack data
    if not os.path.exists(stack_dir):
        raise HTTPException(status_code=404, detail=f"Stack '{stack_id}' not found.")
    if not os.path.exists(inventory_path):
        raise HTTPException(
            status_code=400, detail=f"Inventory file not found for stack '{stack_id}'."
        )
    if not os.path.exists(ssh_key_path):
        raise HTTPException(
            status_code=400, detail=f"SSH key not found for stack '{stack_id}'."
        )

    # Run Ansible command
    try:
        command = [
            "ansible",
            "-m",
            "ping",
            "all",
            "-i",
            inventory_path,
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
            # Match host line
            host_match = re.match(r"^(\S+)\s\|\sSUCCESS\s=>\s{", line)
            if host_match:
                # Save previous host's output
                if host_output:
                    structured_output.append(host_output)
                host_output = {
                    "host": host_match.group(1).strip(),
                    "details": {"raw_output": ""},
                }
            elif host_output:
                # Append subsequent lines to the current host's raw_output
                host_output["details"]["raw_output"] += line + "\n"

        # Append the last host's output
        if host_output:
            structured_output.append(host_output)

        # Parse raw JSON if possible
        for host in structured_output:
            try:
                host["details"] = json.loads(host["details"]["raw_output"].strip())
            except json.JSONDecodeError:
                # Keep raw_output if parsing fails
                pass

        return {
            "message": "Ansible ping test successful",
            "results": structured_output,
        }

    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error running Ansible test: {str(e)}"
        )


@app.get("/stacks/{stack_id}/indexes")
async def get_indexes(stack_id: str):
    indexes = load_indexes(stack_id)
    return {"stack_id": stack_id, "indexes": indexes}


@app.post("/stacks/{stack_id}/indexes")
async def add_index(
    stack_id: str,
    splunk_username: str = Body(..., embed=True),
    splunk_password: str = Body(..., embed=True),
    name: str = Body(..., embed=True),
    maxDataSizeMB: int = Body(None, embed=True),
    datatype: str = Body(None, embed=True),
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
    indexes = load_indexes(stack_id)
    if name in indexes:
        raise HTTPException(status_code=400, detail="Index already exists.")
    indexes[name] = {"maxDataSizeMB": maxDataSizeMB, "datatype": datatype}
    save_indexes(stack_id, indexes)

    # Prepare Ansible variables
    stack_details = load_stack_file(stack_id)

    # Get the inventory path
    _, inventory_path, _ = get_stack_paths(stack_id)

    ansible_vars = {
        "target_node": "",
        "index_name": name,
        "maxDataSizeMB": maxDataSizeMB,
        "datatype": datatype,
        "file_path": "",
    }

    if stack_details["enterprise_deployment_type"] == "distributed":
        # Push to cluster manager
        ansible_vars["target_node"] = stack_details["cluster_manager_node"]
        ansible_vars["file_path"] = (
            "/opt/splunk/etc/manager-apps/001_splunk_aem/local/indexes.conf"
        )
        run_ansible_playbook(
            stack_id,
            "add_index.yml",
            inventory_path,
            ansible_vars=ansible_vars,
            limit=stack_details["cluster_manager_node"],
            creds={"username": splunk_username, "password": splunk_password},
        )

        # Apply cluster bundle
        run_ansible_playbook(
            stack_id,
            "apply_cluster_bundle.yml",
            inventory_path,
            limit=stack_details["cluster_manager_node"],
            creds={"username": splunk_username, "password": splunk_password},
        )

        # Push to SHC if enabled
        if stack_details["shc_cluster"]:
            ansible_vars["shc_deployer_node"] = stack_details["shc_deployer_node"]
            ansible_vars["shc_members"] = stack_details["shc_members"]
            ansible_vars["file_path"] = (
                "/opt/splunk/etc/shcluster/apps/001_splunk_aem/local/indexes.conf"
            )
            run_ansible_playbook(
                stack_id,
                "add_index.yml",
                inventory_path,
                ansible_vars=ansible_vars,
                limit=stack_details["shc_deployer_node"],
                creds={"username": splunk_username, "password": splunk_password},
            )

        # Apply SHC bundle if SHC cluster is enabled
        if stack_details["shc_cluster"]:
            run_ansible_playbook(
                stack_id,
                "apply_shc_bundle.yml",
                inventory_path,
                ansible_vars=ansible_vars,
                limit=stack_details["shc_deployer_node"],
                creds={"username": splunk_username, "password": splunk_password},
            )

    else:
        # Standalone
        ansible_vars["target_node"] = "all"
        ansible_vars["file_path"] = (
            "/opt/splunk/etc/apps/001_splunk_aem/local/indexes.conf"
        )
        run_ansible_playbook(
            stack_id,
            "add_index.yml",
            inventory_path,
            ansible_vars=ansible_vars,
            limit="all",
            creds={"username": splunk_username, "password": splunk_password},
        )

    if stack_details["enterprise_deployment_type"] == "distributed":
        return {
            "message": "Index added successfully, the cluster bundle was pushed automatically.",
            "index": indexes[name],
        }
    else:
        return {
            "message": "Index added successfully, Splunk must be restarted for this take effect, you can trigger Splunk restart using the restart_splunk endpoint.",
            "index": indexes[name],
        }


@app.post("/stacks/{stack_id}/indexes/{index_name}")
async def delete_index(
    stack_id: str,
    index_name: str,
    splunk_username: str = Body(..., embed=True),
    splunk_password: str = Body(..., embed=True),
):
    # Load indexes and validate
    indexes = load_indexes(stack_id)
    if index_name not in indexes:
        raise HTTPException(status_code=404, detail="Index not found.")

    # Remove index
    del indexes[index_name]
    save_indexes(stack_id, indexes)

    # Prepare Ansible variables
    stack_details = load_stack_file(stack_id)
    file_path = (
        "/opt/splunk/etc/shcluster/apps/001_splunk_aem/local/indexes.conf"
        if stack_details["enterprise_deployment_type"] == "distributed"
        else "/opt/splunk/etc/apps/001_splunk_aem/local/indexes.conf"
    )
    ansible_vars = {
        "index_name": index_name,
        "file_path": file_path,
    }

    # Get the inventory path
    _, inventory_path, _ = get_stack_paths(stack_id)

    if stack_details["enterprise_deployment_type"] == "distributed":
        # Remove from cluster manager
        ansible_vars["file_path"] = (
            "/opt/splunk/etc/manager-apps/001_splunk_aem/local/indexes.conf"
        )
        run_ansible_playbook(
            stack_id,
            "remove_index.yml",
            inventory_path,
            ansible_vars=ansible_vars,
            limit=stack_details["cluster_manager_node"],
            creds={"username": splunk_username, "password": splunk_password},
        )

        # Remove from SHC deployer if enabled
        if stack_details["shc_cluster"]:
            ansible_vars["file_path"] = (
                "/opt/splunk/etc/shcluster/apps/001_splunk_aem/local/indexes.conf"
            )
            run_ansible_playbook(
                stack_id,
                "remove_index.yml",
                inventory_path,
                ansible_vars=ansible_vars,
                limit=stack_details["shc_deployer_node"],
                creds={"username": splunk_username, "password": splunk_password},
            )
    else:
        # Standalone deployment
        ansible_vars["file_path"] = (
            "/opt/splunk/etc/apps/001_splunk_aem/local/indexes.conf"
        )
        run_ansible_playbook(
            stack_id,
            "remove_index.yml",
            inventory_path,
            ansible_vars=ansible_vars,
            limit="all",
            creds={"username": splunk_username, "password": splunk_password},
        )

    if stack_details["enterprise_deployment_type"] == "distributed":
        return {
            "message": "Index deleted successfully, the cluster bundle was pushed automatically.",
            "index": index_name,
        }
    else:
        return {
            "message": "Index deleted successfully, Splunk must be restarted for this take effect, you can trigger Splunk restart using the restart_splunk endpoint.",
            "index": index_name,
        }


@app.get("/stacks/{stack_id}/installed_apps")
async def install_splunk_app(
    stack_id: str,
):
    stack_details = load_stack_file(stack_id)
    stack_dir = ensure_stack_dir(stack_id)
    stack_dir, inventory_path, ssh_key_path = get_stack_paths(stack_id)
    files_dir = os.path.join(stack_dir, "files")
    os.makedirs(files_dir, exist_ok=True)

    # Load the apps file to check for existing installations
    apps_file_path = os.path.join(stack_dir, "stack_apps.json")
    if not os.path.exists(apps_file_path):
        with open(apps_file_path, "w") as f:
            json.dump({}, f)

    with open(apps_file_path, "r") as f:
        installed_apps = json.load(f)

    logging.debug(
        f"Installed apps on stack {stack_id}: {json.dumps(installed_apps, indent=4)}"
    )

    return installed_apps


@app.post("/stacks/{stack_id}/install_splunk_app")
async def install_splunk_app(
    stack_id: str,
    splunk_username: str = Body(..., embed=True),
    splunk_password: str = Body(..., embed=True),
    splunkbase_username: str = Body(..., embed=True),
    splunkbase_password: str = Body(..., embed=True),
    splunkbase_app_id: str = Body(..., embed=True),
    splunkbase_app_name: str = Body(..., embed=True),
    version: str = Body(..., embed=True),
):
    stack_details = load_stack_file(stack_id)
    stack_dir = ensure_stack_dir(stack_id)
    stack_dir, inventory_path, ssh_key_path = get_stack_paths(stack_id)
    files_dir = os.path.join(stack_dir, "files")
    os.makedirs(files_dir, exist_ok=True)

    # Load the apps file to check for existing installations
    apps_file_path = os.path.join(stack_dir, "stack_apps.json")
    if not os.path.exists(apps_file_path):
        with open(apps_file_path, "w") as f:
            json.dump({}, f)

    with open(apps_file_path, "r") as f:
        installed_apps = json.load(f)

    logging.debug(
        f"Installed apps on stack {stack_id}: {json.dumps(installed_apps, indent=4)}"
    )

    # Check if the app is already installed with the requested version
    if (
        splunkbase_app_name in installed_apps
        and installed_apps[splunkbase_app_name]["version"] == version
    ):
        return {
            "message": f"App '{splunkbase_app_name}' is already installed with version {version}.",
            "app_details": installed_apps[splunkbase_app_name],
        }

    else:
        logging.debug(
            f"App {splunkbase_app_name} is not installed with version {version}, downloading and installing will be requested."
        )

    # Path to the downloaded tarball
    app_tar_path = os.path.join(files_dir, f"{splunkbase_app_name}.tgz")

    # Log in to Splunk Base
    session_id = login_splunkbase(
        splunkbase_username, splunkbase_password, proxy_dict={}
    )

    # Download app tarball
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

    # Ensure Ansible's files directory exists
    ansible_files_dir = "/app/ansible/files"
    os.makedirs(ansible_files_dir, exist_ok=True)

    # Copy tarball to Ansible's files directory
    ansible_tar_path = os.path.join(ansible_files_dir, f"{splunkbase_app_name}.tgz")
    shutil.copy(app_tar_path, ansible_tar_path)

    # Run Ansible playbook
    playbook = (
        "install_standalone_app.yml"
        if stack_details["enterprise_deployment_type"] == "standalone"
        else "install_shc_app.yml"
    )
    ansible_vars = {
        "splunk_app_name": splunkbase_app_name,
    }

    if stack_details["enterprise_deployment_type"] != "standalone":
        ansible_vars.update({"shc_deployer_node": stack_details["shc_deployer_node"]})

    run_ansible_playbook(
        stack_id,
        playbook,
        inventory_path,
        ansible_vars=ansible_vars,
        creds={"username": splunk_username, "password": splunk_password},
    )

    # Update the apps file
    installed_apps[splunkbase_app_name] = {"id": splunkbase_app_id, "version": version}
    with open(apps_file_path, "w") as f:
        json.dump(installed_apps, f, indent=4)

    # Apply SHC bundle if needed
    if stack_details["shc_cluster"]:
        ansible_vars = {}
        ansible_vars["shc_deployer_node"] = stack_details["shc_deployer_node"]
        ansible_vars["shc_members"] = stack_details["shc_members"]
        run_ansible_playbook(
            stack_id,
            "apply_shc_bundle.yml",
            inventory_path,
            ansible_vars=ansible_vars,
            limit=stack_details["shc_deployer_node"],
            creds={"username": splunk_username, "password": splunk_password},
        )

    return {
        "message": "App installed successfully",
        "app_details": installed_apps[splunkbase_app_name],
    }


@app.delete("/stacks/{stack_id}/delete_splunk_app")
async def delete_splunk_app(
    stack_id: str, splunkbase_app_name: str = Body(..., embed=True)
):
    splunk_username: str = (Body(..., embed=True),)
    splunk_password: str = (Body(..., embed=True),)
    stack_details = load_stack_file(stack_id)
    stack_dir = ensure_stack_dir(stack_id)
    apps_file_path = os.path.join(stack_dir, "stack_apps.json")
    stack_dir, inventory_path, ssh_key_path = get_stack_paths(stack_id)

    # Load the apps file
    if not os.path.exists(apps_file_path):
        raise HTTPException(
            status_code=404, detail="No apps file found for this stack."
        )

    with open(apps_file_path, "r") as f:
        installed_apps = json.load(f)

    # Check if app exists in the stack
    if splunkbase_app_name not in installed_apps:
        raise HTTPException(
            status_code=404, detail="App not found in this stack's installed apps."
        )

    # Run Ansible playbook for app removal
    playbook = (
        "remove_standalone_app.yml"
        if stack_details["enterprise_deployment_type"] == "standalone"
        else "remove_shc_app.yml"
    )
    ansible_vars = {
        "splunk_app_name": splunkbase_app_name,
    }

    if stack_details["enterprise_deployment_type"] != "standalone":
        ansible_vars.update({"shc_deployer_node": stack_details["shc_deployer_node"]})

    run_ansible_playbook(
        stack_id,
        playbook,
        inventory_path,
        ansible_vars=ansible_vars,
        creds={"username": splunk_username, "password": splunk_password},
    )

    # If SHC, apply the bundle
    if stack_details["shc_cluster"]:
        run_ansible_playbook(
            stack_id,
            "apply_shc_bundle.yml",
            inventory_path,
            ansible_vars={},
            limit=stack_details["shc_deployer_node"],
            creds={"username": splunk_username, "password": splunk_password},
        )

    # Update the apps JSON file
    del installed_apps[splunkbase_app_name]
    with open(apps_file_path, "w") as f:
        json.dump(installed_apps, f, indent=4)

    return {
        "message": f"App {splunkbase_app_name} deleted successfully",
        "remaining_apps": installed_apps,
    }


@app.post("/stacks/{stack_id}/shc_rolling_restart")
async def shc_rolling_restart(
    stack_id: str,
    splunk_username: str = Body(..., embed=True),
    splunk_password: str = Body(..., embed=True),
):
    stack_details = load_stack_file(stack_id)
    stack_dir, inventory_path, ssh_key_path = get_stack_paths(stack_id)

    # Only for SHC
    if stack_details["enterprise_deployment_type"] == "standalone":
        raise HTTPException(
            status_code=400, detail="SHC cluster is not enabled for this stack."
        )

    # Trigger Rolling Restart
    ansible_vars = {}
    ansible_vars["shc_deployer_node"] = stack_details["shc_deployer_node"]
    ansible_vars["shc_members"] = stack_details["shc_members"]
    run_ansible_playbook(
        stack_id,
        "trigger_shc_rolling_restart.yml",
        inventory_path,
        ansible_vars=ansible_vars,
        limit=stack_details["shc_deployer_node"],
        creds={"username": splunk_username, "password": splunk_password},
    )

    return {
        "message": "SHC Rolling Restart triggered successfully",
    }


@app.post("/stacks/{stack_id}/cluster_rolling_restart")
async def cluster_rolling_restart(
    stack_id: str,
    splunk_username: str = Body(..., embed=True),
    splunk_password: str = Body(..., embed=True),
):
    stack_details = load_stack_file(stack_id)
    stack_dir, inventory_path, ssh_key_path = get_stack_paths(stack_id)

    # Only for SHC
    if stack_details["enterprise_deployment_type"] == "standalone":
        raise HTTPException(
            status_code=400, detail="Indexer cluster is not enabled for this stack."
        )

    # Trigger Rolling Restart
    ansible_vars = {}
    ansible_vars["target_node"] = stack_details["cluster_manager_node"]
    run_ansible_playbook(
        stack_id,
        "trigger_cluster_rolling_restart.yml",
        inventory_path,
        ansible_vars=ansible_vars,
        limit=stack_details["cluster_manager_node"],
        creds={"username": splunk_username, "password": splunk_password},
    )

    return {
        "message": "Indexer Cluster Rolling Restart triggered successfully",
    }


@app.post("/stacks/{stack_id}/restart_splunk")
async def restart_splunk(
    stack_id: str,
    limit: str = Body(None, embed=True),  # Optional limit parameter
):
    stack_details = load_stack_file(stack_id)
    stack_dir, inventory_path, ssh_key_path = get_stack_paths(stack_id)

    # Trigger Splunk service restart
    ansible_vars = {}

    # Format the limit if provided
    limit_hosts = None
    if limit:
        if isinstance(limit, str):
            limit_hosts = ",".join([host.strip() for host in limit.split(",")])

    # If environment is distributed, limit is mandatory
    if stack_details["enterprise_deployment_type"] != "standalone":
        if not limit_hosts:
            raise HTTPException(
                status_code=400,
                detail="Limit parameter is required for distributed deployments.",
            )

    run_ansible_playbook(
        stack_id,
        "restart_splunk.yml",
        inventory_path,
        ansible_vars=ansible_vars,
        limit=limit_hosts,
        creds=None,
    )

    return {
        "message": f"Splunk Restart triggered successfully for {'specified hosts' if limit_hosts else 'all hosts'}.",
    }
