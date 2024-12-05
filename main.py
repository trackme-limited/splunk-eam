from fastapi import FastAPI, HTTPException, Body
from pydantic import BaseModel
from typing import Dict
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
    shc_cluster: bool
    cluster_manager_node: str = None  # Optional unless distributed
    shc_deployer_node: str = None  # Optional unless shc_cluster
    shc_members: list = None  # Optional unless shc_cluster
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


def run_ansible_playbook(
    stack_id: str,
    playbook_name: str,
    inventory_path: str,
    ansible_vars: dict = None,
    limit: str = None,
):
    stack_dir, inventory_path, ssh_key_path = get_stack_paths(stack_id)
    creds_file_path = os.path.join(stack_dir, "splunk_creds.json")

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

    # Ensure the credentials file exists if required
    if "apply_cluster_bundle" in playbook_name or "apply_shc_bundle" in playbook_name:
        if not os.path.exists(creds_file_path):
            raise HTTPException(
                status_code=400,
                detail=f"Splunk credentials not found for stack '{stack_id}'. Please add them using the /splunk_credentials endpoint.",
            )

        # Inject credentials into ansible_vars
        with open(creds_file_path, "r") as creds_file:
            creds = json.load(creds_file)
        if ansible_vars is None:
            ansible_vars = {}
        ansible_vars.update(
            {
                "splunk_username": creds["username"],
                "splunk_password": creds["password"],
            }
        )

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
        command.extend(["--limit", limit])  # Add --limit option if specified

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

    # Validate SHC deployer node if SHC cluster is true
    if stack.shc_cluster and not stack.shc_deployer_node:
        raise HTTPException(
            status_code=400,
            detail="shc_deployer_node is required for SHC cluster setups.",
        )

    # Validate SHC members if SHC cluster is true
    if stack.shc_cluster and not stack.shc_members:
        raise HTTPException(
            status_code=400,
            detail="shc_members is required for SHC cluster setups.",
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


def ensure_stack_dir(stack_id: str):
    stack_dir = os.path.join(DATA_DIR, stack_id)
    os.makedirs(stack_dir, exist_ok=True)
    return stack_dir


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


@app.post("/stacks/{stack_id}/splunk_credentials")
async def set_splunk_credentials(
    stack_id: str, username: str = Body(...), password: str = Body(...)
):
    # Ensure stack exists
    stack_dir = ensure_stack_dir(stack_id)
    creds_path = os.path.join(stack_dir, "splunk_creds.json")

    # Store credentials securely
    try:
        creds = {"username": username, "password": password}
        with open(creds_path, "w") as f:
            json.dump(creds, f)
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error saving credentials: {str(e)}"
        )

    return {"message": f"Credentials saved for stack '{stack_id}'"}


@app.post("/stacks/{stack_id}/ansible_test")
async def ansible_test(stack_id: str):
    stack_dir, inventory_path, ssh_key_path = get_stack_paths(stack_id)
    creds_file_path = os.path.join(stack_dir, "splunk_creds.json")

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
        )

        # Apply cluster bundle
        run_ansible_playbook(
            stack_id,
            "apply_cluster_bundle.yml",
            inventory_path,
            limit=stack_details["cluster_manager_node"],
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
            )

        # Apply SHC bundle if SHC cluster is enabled
        if stack_details["shc_cluster"]:
            run_ansible_playbook(
                stack_id,
                "apply_shc_bundle.yml",
                inventory_path,
                ansible_vars=ansible_vars,
                limit=stack_details["shc_deployer_node"],
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
        )

    return {"message": "Index added successfully", "index": indexes[name]}


@app.delete("/stacks/{stack_id}/indexes/{index_name}")
async def delete_index(stack_id: str, index_name: str):
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
        )

    return {"message": "Index deleted successfully"}


@app.post("/stacks/{stack_id}/install_splunk_app")
async def install_splunk_app(
    stack_id: str,
    splunkbase_username: str = Body(..., embed=True),
    splunkbase_password: str = Body(..., embed=True),
    splunkbase_app_id: str = Body(..., embed=True),
    splunkbase_app_name: str = Body(..., embed=True),
    version: str = Body(..., embed=True),
):
    stack_details = load_stack_file(stack_id)
    stack_dir = ensure_stack_dir(stack_id)
    apps_file_path = os.path.join(stack_dir, "stack_apps.json")
    stack_dir, inventory_path, ssh_key_path = get_stack_paths(stack_id)

    # Load or create the apps file
    if not os.path.exists(apps_file_path):
        with open(apps_file_path, "w") as f:
            json.dump({}, f)

    with open(apps_file_path, "r") as f:
        installed_apps = json.load(f)

    # Check if app is already installed
    if splunkbase_app_name in installed_apps:
        if installed_apps[splunkbase_app_name]["version"] == version:
            return {
                "message": "App already installed",
                "app_details": installed_apps[splunkbase_app_name],
            }

    # Log in to Splunk Base
    session_id = login_splunkbase(
        splunkbase_username, splunkbase_password, proxy_dict={}
    )

    # Download app tarball
    app_download_url = f"https://splunkbase.splunk.com/api/v2/app/{splunkbase_app_id}/release/{version}/download/"
    response = requests.get(
        app_download_url, headers={"X-Auth-Token": session_id}, stream=True
    )

    logging.debug(
        f"Splunkbase app download response status code: {response.status_code} response: {response.status_code}"
    )

    if response.status_code != 200:
        raise HTTPException(
            status_code=500, detail="Failed to download app from Splunk Base"
        )

    app_tar_path = os.path.join(stack_dir, f"{splunkbase_app_name}.tgz")
    with open(app_tar_path, "wb") as f:
        f.write(response.content)

    # Run Ansible playbook
    playbook = (
        "install_standalone_app.yml"
        if stack_details["enterprise_deployment_type"] == "standalone"
        else "install_shc_app.yml"
    )
    ansible_vars = {
        "splunk_app_tarball": app_tar_path,
        "splunk_app_name": splunkbase_app_name,
    }

    if stack_details["enterprise_deployment_type"] != "standalone":
        ansible_vars.update({"shc_deployer_node": stack_details["shc_deployer_node"]})

    run_ansible_playbook(stack_id, playbook, inventory_path, ansible_vars=ansible_vars)

    # Update apps JSON
    installed_apps[splunkbase_app_name] = {"id": splunkbase_app_id, "version": version}
    with open(apps_file_path, "w") as f:
        json.dump(installed_apps, f, indent=4)

    # Apply SHC bundle if needed
    if stack_details["shc_cluster"]:
        run_ansible_playbook(
            stack_id,
            "apply_shc_bundle.yml",
            inventory_path,
            ansible_vars={},
            limit=stack_details["shc_deployer_node"],
        )

    return {
        "message": "App installed successfully",
        "app_details": installed_apps[splunkbase_app_name],
    }


@app.delete("/stacks/{stack_id}/delete_splunk_app")
async def delete_splunk_app(
    stack_id: str, splunkbase_app_name: str = Body(..., embed=True)
):
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

    run_ansible_playbook(stack_id, playbook, inventory_path, ansible_vars=ansible_vars)

    # If SHC, apply the bundle
    if stack_details["shc_cluster"]:
        run_ansible_playbook(
            stack_id,
            "apply_shc_bundle.yml",
            inventory_path,
            ansible_vars={},
            limit=stack_details["shc_deployer_node"],
        )

    # Update the apps JSON file
    del installed_apps[splunkbase_app_name]
    with open(apps_file_path, "w") as f:
        json.dump(installed_apps, f, indent=4)

    return {
        "message": f"App {splunkbase_app_name} deleted successfully",
        "remaining_apps": installed_apps,
    }
