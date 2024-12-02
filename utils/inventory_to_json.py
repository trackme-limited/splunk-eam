import json
import re

def parse_ansible_inventory(file_path):
    inventory = {"all": {"hosts": {}}}
    current_group = "all"

    with open(file_path, "r") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("[") and line.endswith("]"):
                current_group = line[1:-1]
                if current_group not in inventory:
                    inventory[current_group] = {"hosts": {}}
            else:
                match = re.match(r"(\S+)\s+(.*)", line)
                if match:
                    hostname = match.group(1)
                    vars_line = match.group(2)
                    vars_dict = {}
                    for var in vars_line.split():
                        key, value = var.split("=")
                        vars_dict[key] = value
                    inventory[current_group]["hosts"][hostname] = vars_dict

    return inventory

def save_inventory_as_json(inventory, output_file):
    with open(output_file, "w") as f:
        json.dump(inventory, f, indent=4)

if __name__ == "__main__":
    input_file = "inventory.ini"  # Replace with your Ansible inventory file
    output_file = "inventory.json"
    inventory_data = parse_ansible_inventory(input_file)
    save_inventory_as_json(inventory_data, output_file)
    print(f"Converted inventory saved to {output_file}")
