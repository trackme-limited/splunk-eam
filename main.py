from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Dict
import json
import os

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