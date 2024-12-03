from fastapi import FastAPI, HTTPException, Body
from pydantic import BaseModel
from typing import Dict
import json
import os
import subprocess
import base64

app = FastAPI()

# Directory to store stack files
DATA_DIR = "data"
MAIN_FILE = os.path.join(DATA_DIR, "stacks.json")

# Ensure data directory and main file exist
os.makedirs(DATA_DIR, exist_ok=True)
if not os.path.exists(MAIN_FILE):
    with open(MAIN_FILE, "w") as f:
        json.dump({"stacks": []}, f)

# Model for stack metadata
class Stack(BaseModel):
    stack_id: str
    enterprise_deployment_type: str
    shc_cluster: bool

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


# GET /stacks
@app.get("/stacks")
def get_all_stacks():
    main_data = load_main_file()
    return {"stacks": main_data.get("stacks", [])}

# POST /stacks
@app.post("/stacks")
def create_stack(stack: Stack):
    main_data = load_main_file()
    if stack.stack_id in [s["stack_id"] for s in main_data["stacks"]]:
        raise HTTPException(status_code=400, detail="Stack ID already exists.")
    
    # Add to main file
    main_data["stacks"].append({"stack_id": stack.stack_id})
    save_main_file(main_data)
    
    # Create individual stack file
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
        raise HTTPException(status_code=500, detail=f"Error reading inventory: {str(e)}")
    
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
                    vars_line = " ".join(f"{key}={value}" for key, value in vars_dict.items())
                    f.write(f"{host} {vars_line}\n")
                f.write("\n")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error saving inventory: {str(e)}")
    
    return {"message": f"Inventory for stack '{stack_id}' saved successfully", "path": inventory_path}

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
    
    return {"message": f"SSH key for stack '{stack_id}' saved successfully", "path": ssh_key_path}

@app.post("/stacks/{stack_id}/ansible_test")
@app.post("/stacks/{stack_id}/ansible_test")
async def ansible_test(stack_id: str):
    stack_dir, inventory_path, ssh_key_path = get_stack_paths(stack_id)

    # Validate stack data
    if not os.path.exists(stack_dir):
        raise HTTPException(status_code=404, detail=f"Stack '{stack_id}' not found.")
    if not os.path.exists(inventory_path):
        raise HTTPException(status_code=400, detail=f"Inventory file not found for stack '{stack_id}'.")
    if not os.path.exists(ssh_key_path):
        raise HTTPException(status_code=400, detail=f"SSH key not found for stack '{stack_id}'.")

    # Temporary directory for Ansible
    ansible_tmp_dir = os.path.join(DATA_DIR, "ansible_tmp")
    os.makedirs(ansible_tmp_dir, exist_ok=True)

    # Run Ansible command
    try:
        command = [
            "ansible", "-m", "ping", "all",
            "-i", inventory_path,
            "--private-key", ssh_key_path,
            "-e", "ansible_ssh_extra_args='-o StrictHostKeyChecking=no'"
        ]
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env={
                **os.environ,  # Keep existing environment variables
                "ANSIBLE_LOCAL_TEMP": ansible_tmp_dir,
            },
        )
        if result.returncode != 0:
            raise HTTPException(
                status_code=500,
                detail=f"Ansible command failed: {result.stderr.strip()}"
            )
        return {"message": "Ansible ping test successful", "output": result.stdout.strip()}
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
    datatype: str = Body(None, embed=True)
):
    # Validate inputs
    if datatype not in [None, "event", "metric"]:
        raise HTTPException(status_code=400, detail="Invalid datatype. Must be 'event' or 'metric'.")
    
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
    file_path = (
        "/opt/splunk/etc/shcluster/apps/001_splunk_aem/local/indexes.conf"
        if stack_details["enterprise_deployment_type"] == "distributed"
        else "/opt/splunk/etc/apps/001_splunk_aem/local/indexes.conf"
    )
    ansible_vars = {
        "index_name": name,
        "maxDataSizeMB": maxDataSizeMB,
        "datatype": datatype,
        "file_path": file_path,
    }

    # Run Ansible playbook
    playbook_dir = "/app/ansible"
    try:
        command = [
            "ansible-playbook", f"{playbook_dir}/add_index.yml",
            "-e", json.dumps(ansible_vars)
        ]
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if result.returncode != 0:
            raise HTTPException(status_code=500, detail=f"Ansible playbook failed: {result.stderr.strip()}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error running Ansible playbook: {str(e)}")
    
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

    # Run Ansible playbook
    playbook_dir = "/app/ansible"
    try:
        command = [
            "ansible-playbook", f"{playbook_dir}/remove_index.yml",
            "-e", json.dumps(ansible_vars)
        ]
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if result.returncode != 0:
            raise HTTPException(status_code=500, detail=f"Ansible playbook failed: {result.stderr.strip()}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error running Ansible playbook: {str(e)}")
    
    return {"message": "Index deleted successfully"}
